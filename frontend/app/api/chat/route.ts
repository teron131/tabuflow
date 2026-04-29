import {
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

function buildMessageChunks({
	content,
	request,
	payload,
	isError = false,
}: {
	content: string;
	request: { message: string; model?: string };
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
	};
	const backendResponse = await fetch(new URL("/api/chat", API_BASE), {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(backendRequest),
	});
	const payload = await readPayload(backendResponse);

	if (!backendResponse.ok) {
		const content = detailMessage(payload, backendResponse.status);
		return responseStream(
			buildMessageChunks({
				content,
				request: backendRequest,
				payload,
				isError: true,
			}),
		);
	}

	const content =
		payload.content?.trim() || "Python workbench returned no text.";
	return responseStream(
		buildMessageChunks({
			content,
			request: backendRequest,
			payload,
		}),
	);
}
