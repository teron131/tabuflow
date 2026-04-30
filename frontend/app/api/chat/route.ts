import {
	createUIMessageStream,
	createUIMessageStreamResponse,
	type UIMessage,
	type UIMessageChunk,
} from "ai";
import type { BackendChatResponse } from "@/lib/chat-contracts";
import {
	buildDemoTraceChunks,
	parseDemoTraceCommand,
} from "./demo-trace-stream";

const API_BASE = process.env.DATA_AGENTICS_API_URL || "http://localhost:8017";
const BACKEND_CHAT_TOOL_NAME = "backendChat";

type BackendChatRequest = {
	message: string;
	messages?: unknown;
	model?: string;
	source_files?: string[];
};

function extractTextPart(part: unknown) {
	if (!part || typeof part !== "object") {
		return "";
	}
	const candidate = part as { type?: unknown; text?: unknown };
	return candidate.type === "text" && typeof candidate.text === "string"
		? candidate.text
		: "";
}

function latestUserText(messages: UIMessage[]) {
	const latest = [...messages]
		.reverse()
		.find((message) => message.role === "user");
	return (
		latest?.parts.map(extractTextPart).filter(Boolean).join("\n").trim() || ""
	);
}

function backendMessages(messages: UIMessage[]) {
	return messages
		.filter(
			(message) => message.role === "user" || message.role === "assistant",
		)
		.map((message) => ({
			role: message.role as "user" | "assistant",
			content: message.parts
				.map(extractTextPart)
				.filter(Boolean)
				.join("\n")
				.trim(),
		}))
		.filter((message) => message.content.length > 0);
}

function sourceFilesFromBody(value: unknown) {
	if (!Array.isArray(value)) {
		return [];
	}
	return value.filter((item): item is string => typeof item === "string");
}

function detailMessage(payload: BackendChatResponse, status: number) {
	return (
		payload.detail?.message ||
		payload.content ||
		`Python workbench returned HTTP ${status}.`
	);
}

function sleep(delayMs: number) {
	return new Promise((resolve) => setTimeout(resolve, delayMs));
}

function streamChunks(chunks: UIMessageChunk[], delayMs = 0) {
	return new ReadableStream<UIMessageChunk>({
		async start(controller) {
			for (const chunk of chunks) {
				controller.enqueue(chunk);
				if (delayMs > 0) {
					await sleep(delayMs);
				}
			}
			controller.close();
		},
	});
}

function responseStream(chunks: UIMessageChunk[], delayMs = 0) {
	return createUIMessageStreamResponse({
		stream: streamChunks(chunks, delayMs),
	});
}

async function* readNdjsonUiMessageChunks(
	body: ReadableStream<Uint8Array>,
): AsyncGenerator<UIMessageChunk> {
	const reader = body.getReader();
	const decoder = new TextDecoder();
	let buffer = "";

	try {
		while (true) {
			const { done, value } = await reader.read();
			if (done) {
				break;
			}
			buffer += decoder.decode(value, { stream: true });
			const lines = buffer.split("\n");
			buffer = lines.pop() || "";
			for (const line of lines) {
				const trimmedLine = line.trim();
				if (!trimmedLine) {
					continue;
				}
				yield JSON.parse(trimmedLine) as UIMessageChunk;
			}
		}

		buffer += decoder.decode();
		const trailingLine = buffer.trim();
		if (trailingLine) {
			yield JSON.parse(trailingLine) as UIMessageChunk;
		}
	} finally {
		reader.releaseLock();
	}
}

function backendStreamResponse(body: ReadableStream<Uint8Array>) {
	return createUIMessageStreamResponse({
		stream: createUIMessageStream({
			execute: async ({ writer }) => {
				for await (const chunk of readNdjsonUiMessageChunks(body)) {
					writer.write(chunk);
				}
			},
			onError: () => "Python workbench stream failed.",
		}),
	});
}

async function readPayload(response: Response): Promise<BackendChatResponse> {
	try {
		return (await response.json()) as BackendChatResponse;
	} catch {
		return {
			status: "error",
			detail: { message: "Python workbench returned a non-JSON response." },
		};
	}
}

function errorPayload(message: string): BackendChatResponse {
	return {
		status: "error",
		detail: { message },
	};
}

function buildMessageChunks({
	content,
	request,
	payload,
	isError = false,
}: {
	content: string;
	request: BackendChatRequest;
	payload: BackendChatResponse;
	isError?: boolean;
}): UIMessageChunk[] {
	const messageId = crypto.randomUUID();
	const textId = crypto.randomUUID();
	const toolCallId = crypto.randomUUID();
	const toolInput: UIMessageChunk = {
		type: "tool-input-available",
		toolCallId,
		toolName: BACKEND_CHAT_TOOL_NAME,
		input: request,
	};
	const toolOutput: UIMessageChunk = isError
		? {
				type: "tool-output-error",
				toolCallId,
				errorText: content,
			}
		: {
				type: "tool-output-available",
				toolCallId,
				output: payload,
			};

	return [
		{ type: "start", messageId },
		{ type: "text-start", id: textId },
		{ type: "text-delta", id: textId, delta: content },
		{ type: "text-end", id: textId },
		toolInput,
		toolOutput,
		{ type: "finish", finishReason: isError ? "error" : "stop" },
	];
}

function backendErrorResponse({
	content,
	request,
	payload,
}: {
	content: string;
	request: BackendChatRequest;
	payload: BackendChatResponse;
}) {
	return responseStream(
		buildMessageChunks({
			content,
			request,
			payload,
			isError: true,
		}),
	);
}

export async function POST(request: Request) {
	const body = await request.json().catch(() => null);
	const messages = Array.isArray(body?.messages)
		? (body.messages as UIMessage[])
		: [];
	const singleMessage = body?.message ? ([body.message] as UIMessage[]) : [];
	const message = latestUserText(messages.length ? messages : singleMessage);
	const selectedModel =
		typeof body?.selectedChatModel === "string"
			? body.selectedChatModel
			: undefined;
	const sourceFiles = sourceFilesFromBody(body?.sourceFiles);

	if (!message) {
		return responseStream([
			{ type: "start", messageId: crypto.randomUUID() },
			{ type: "error", errorText: "No user message was provided." },
			{ type: "finish", finishReason: "error" },
		]);
	}

	const demoTrace = parseDemoTraceCommand(message, body?.demoTraceMode);
	if (demoTrace) {
		return responseStream(
			buildDemoTraceChunks(demoTrace.message),
			demoTrace.delayMs,
		);
	}

	const visibleMessages = messages.length ? messages : singleMessage;
	const backendRequest = {
		message,
		messages: backendMessages(visibleMessages),
		model: selectedModel,
		source_files: sourceFiles,
	};
	const backendResponse = await fetch(new URL("/api/chat/stream", API_BASE), {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(backendRequest),
	});

	if (!backendResponse.ok) {
		const payload = await readPayload(backendResponse);
		return backendErrorResponse({
			content: detailMessage(payload, backendResponse.status),
			request: backendRequest,
			payload,
		});
	}

	if (!backendResponse.body) {
		const payload = errorPayload("Python workbench returned an empty stream.");
		return backendErrorResponse({
			content: detailMessage(payload, backendResponse.status),
			request: backendRequest,
			payload,
		});
	}

	return backendStreamResponse(backendResponse.body);
}
