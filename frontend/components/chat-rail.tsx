"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type FileUIPart } from "ai";
import {
	Bot,
	CheckCircle2,
	ChevronDown,
	CircleUserRound,
	Cloud,
	Loader2,
	Paperclip,
	Send,
	TriangleAlert,
	Wrench,
	X,
	Zap,
} from "lucide-react";
import type {
	ChangeEvent,
	ComponentType,
	ClipboardEvent as ReactClipboardEvent,
	DragEvent as ReactDragEvent,
} from "react";
import {
	memo,
	useCallback,
	useEffect,
	useLayoutEffect,
	useMemo,
	useRef,
	useState,
} from "react";
import type { WorkbenchMessage } from "@/lib/chat-contracts";

type WorkbenchPart = WorkbenchMessage["parts"][number];
type WorkbenchToolPart = Extract<WorkbenchPart, { toolCallId: string }>;
type WorkbenchFilePart = Extract<WorkbenchPart, { type: "file" }>;
type DemoTraceMode = "fast" | "slow";
type TraceEntry = {
	id: string;
	name: string;
	status: string;
	summary: string;
};
type ComposerCommand = {
	id: string;
	label: string;
	description: string;
	mode: DemoTraceMode;
	icon: ComponentType<{ size?: number; className?: string }>;
};
type ComposerAttachment = {
	id: string;
	name: string;
	type: string;
	previewUrl: string | null;
	sourcePath?: string;
	status: "uploading" | "attached" | "error";
};
type UploadedWorkspaceFile = {
	name: string;
	path: string;
	contentType?: string;
	targetBackend?: string;
};

type ChatRailProps = {
	modelOptions: string[];
	selectedModel: string;
	uploadStatus: string;
	onModelChange: (model: string) => void;
	onUploadFiles: (
		files: File[],
	) => Promise<UploadedWorkspaceFile[]> | UploadedWorkspaceFile[];
};

const initialMessages: WorkbenchMessage[] = [
	{
		id: "welcome",
		role: "assistant",
		parts: [
			{
				type: "text",
				text: "Data workbench online. Ask about prepared files, SQL targets, or the current result set.",
			},
		],
	},
];

const composerCommands: ComposerCommand[] = [
	{
		id: "demo-trace",
		label: "demoTrace",
		description: "Run staged tool trace",
		mode: "slow",
		icon: Cloud,
	},
	{
		id: "demo-trace-fast",
		label: "demoTraceFast",
		description: "Run fast staged tool trace",
		mode: "fast",
		icon: Zap,
	},
];

const CHAT_BOTTOM_THRESHOLD = 28;
const ACCEPTED_UPLOAD_TYPES = ".csv,.xlsx,.pdf,image/*";
const TEXTAREA_MAX_HEIGHT_VAR = "--composer-textarea-max-height";
const DEFAULT_TEXTAREA_MAX_HEIGHT = 150;

function isScrolledNearBottom(element: HTMLElement) {
	return (
		element.scrollHeight - element.scrollTop - element.clientHeight <=
		CHAT_BOTTOM_THRESHOLD
	);
}

function hasDraggedFiles(event: ReactDragEvent) {
	return Array.from(event.dataTransfer.types).includes("Files");
}

function imageFilesFromClipboard(
	event: ReactClipboardEvent<HTMLTextAreaElement>,
) {
	return Array.from(event.clipboardData?.items || [])
		.filter((item) => item.kind === "file" && item.type.startsWith("image/"))
		.map((item) => item.getAsFile())
		.filter((file): file is File => file !== null);
}

function attachmentId(file: File) {
	return `${file.name}-${file.size}-${file.lastModified}-${crypto.randomUUID()}`;
}

function attachmentPreview(file: File) {
	return file.type.startsWith("image/") ? URL.createObjectURL(file) : null;
}

function revokeAttachmentPreview(
	attachment: Pick<ComposerAttachment, "previewUrl">,
	previewUrls: Set<string>,
) {
	if (!attachment.previewUrl) {
		return;
	}
	URL.revokeObjectURL(attachment.previewUrl);
	previewUrls.delete(attachment.previewUrl);
}

function attachmentPrompt(attachments: ComposerAttachment[]) {
	const names = attachments.map((attachment) => attachment.name).join(", ");
	return names ? `Attached files: ${names}` : "";
}

function attachedSourceFiles(attachments: ComposerAttachment[]) {
	return attachments
		.filter((attachment) => attachment.status === "attached")
		.map((attachment) => attachment.sourcePath || attachment.name)
		.filter(Boolean);
}

function messageFileParts(attachments: ComposerAttachment[]): FileUIPart[] {
	return attachments
		.filter(
			(attachment) =>
				attachment.status === "attached" &&
				attachment.previewUrl &&
				attachment.type.startsWith("image/"),
		)
		.map((attachment) => ({
			type: "file",
			mediaType: attachment.type,
			filename: attachment.name,
			url: attachment.previewUrl || "",
		}));
}

function fitComposerTextarea(textarea: HTMLTextAreaElement | null) {
	if (!textarea) {
		return;
	}
	const styles = window.getComputedStyle(textarea);
	const maxHeight =
		Number.parseFloat(styles.getPropertyValue(TEXTAREA_MAX_HEIGHT_VAR)) ||
		DEFAULT_TEXTAREA_MAX_HEIGHT;
	textarea.style.height = "auto";
	textarea.style.height = `${Math.min(textarea.scrollHeight, maxHeight)}px`;
	textarea.style.overflowY =
		textarea.scrollHeight > maxHeight ? "auto" : "hidden";
}

function textFromMessage(message: WorkbenchMessage) {
	return message.parts
		.filter((part) => part.type === "text")
		.map((part) => part.text)
		.join("\n");
}

function isToolPart(part: WorkbenchPart): part is WorkbenchToolPart {
	return part.type.startsWith("tool-") || part.type === "dynamic-tool";
}

function isFilePart(part: WorkbenchPart): part is WorkbenchFilePart {
	return part.type === "file";
}

function formatJson(value: unknown) {
	return JSON.stringify(value, null, 2);
}

function toolTitle(part: WorkbenchToolPart) {
	return part.type === "dynamic-tool"
		? part.toolName
		: part.type.replace(/^tool-/, "");
}

function ToolMessage({ part }: { part: WorkbenchToolPart }) {
	const input = "input" in part ? part.input : undefined;
	const output = "output" in part ? part.output : undefined;
	const errorText = "errorText" in part ? part.errorText : undefined;
	const partRecord = part as { state?: unknown; type: string };
	const state =
		typeof partRecord.state === "string" ? partRecord.state : partRecord.type;
	const isError =
		state === "output-error" || partRecord.type === "tool-output-error";
	const trace = backendTrace(output);
	const conversation = conversationTrace(output);

	return (
		<details className="tool-message">
			<summary>
				<span className="tool-message-icon">
					{isError ? <TriangleAlert size={13} /> : <Wrench size={13} />}
				</span>
				<span className="tool-summary-main">
					<span>{toolTitle(part)}</span>
					<span className="tool-summary-steps">{traceSummary(trace)}</span>
				</span>
				<span className={isError ? "tool-state error" : "tool-state"}>
					{state.replaceAll("-", " ")}
				</span>
				<ChevronDown size={13} className="tool-chevron" />
			</summary>
			<div className="tool-message-body">
				{trace.length > 0 && (
					<section className="backend-trace">
						{trace.map((item) => (
							<div className="trace-row" key={item.id}>
								<span>{item.name}</span>
								<p>
									<b>{item.status}</b>
									{item.summary ? ` · ${item.summary}` : ""}
								</p>
							</div>
						))}
					</section>
				)}
				{trace.length === 0 && !isError && (
					<p className="tool-empty">
						No backend tools were used for this turn.
					</p>
				)}
				{errorText && (
					<div>
						<span className="field-label">error</span>
						<pre>{errorText}</pre>
					</div>
				)}
				{conversation.length > 0 && (
					<details className="payload-details">
						<summary>Conversation messages</summary>
						<section className="conversation-trace">
							{conversation.map((item) => (
								<div className="trace-row" key={item.id}>
									<span>{item.role}</span>
									<p>{item.content}</p>
								</div>
							))}
						</section>
					</details>
				)}
				{(input !== undefined || output !== undefined) && (
					<details className="payload-details">
						<summary>Payload details</summary>
						{input !== undefined && (
							<div>
								<span className="field-label">input</span>
								<pre>{formatJson(input)}</pre>
							</div>
						)}
						{output !== undefined && (
							<div>
								<span className="field-label">output</span>
								<pre>{formatJson(output)}</pre>
							</div>
						)}
					</details>
				)}
			</div>
		</details>
	);
}

function artifactFromOutput(output: unknown) {
	if (!output || typeof output !== "object") {
		return null;
	}
	const artifact = (output as { artifact?: unknown }).artifact;
	return artifact && typeof artifact === "object" ? artifact : null;
}

function traceItems(value: unknown): TraceEntry[] {
	if (!Array.isArray(value)) {
		return [];
	}
	return value
		.map((item) => {
			if (!item || typeof item !== "object") {
				return null;
			}
			const candidate = item as {
				id?: unknown;
				name?: unknown;
				status?: unknown;
				summary?: unknown;
			};
			if (
				typeof candidate.name !== "string" ||
				typeof candidate.status !== "string"
			) {
				return null;
			}
			return {
				id:
					typeof candidate.id === "string"
						? candidate.id
						: `${candidate.name}-${candidate.status}-${candidate.summary || ""}`,
				name: candidate.name,
				status: candidate.status,
				summary: typeof candidate.summary === "string" ? candidate.summary : "",
			};
		})
		.filter((item): item is TraceEntry => Boolean(item));
}

function backendTrace(output: unknown): TraceEntry[] {
	const artifact = artifactFromOutput(output);
	if (!artifact) {
		return [];
	}
	const toolTrace = traceItems(
		(artifact as { tool_trace?: unknown }).tool_trace,
	);
	if (toolTrace.length > 0) {
		return toolTrace;
	}
	return traceItems((artifact as { stage_trace?: unknown }).stage_trace);
}

function traceSummary(trace: TraceEntry[]) {
	if (trace.length === 0) {
		return "No backend tools";
	}
	const names = [...new Set(trace.map((item) => item.name))];
	const visibleNames = names.slice(0, 3).join(" -> ");
	const remaining = names.length - 3;
	return remaining > 0
		? `${trace.length} steps · ${visibleNames} +${remaining}`
		: `${trace.length} steps · ${visibleNames}`;
}

function conversationTrace(
	output: unknown,
): Array<{ id: string; role: string; content: string }> {
	const artifact = artifactFromOutput(output);
	if (!artifact) {
		return [];
	}
	const trace = (artifact as { conversation_trace?: unknown })
		.conversation_trace;
	if (!Array.isArray(trace)) {
		return [];
	}
	return trace
		.map((item) => {
			if (!item || typeof item !== "object") {
				return null;
			}
			const candidate = item as {
				id?: unknown;
				role?: unknown;
				content?: unknown;
			};
			if (
				typeof candidate.role !== "string" ||
				typeof candidate.content !== "string"
			) {
				return null;
			}
			return {
				id:
					typeof candidate.id === "string"
						? candidate.id
						: `${candidate.role}-${candidate.content}`,
				role: candidate.role,
				content: candidate.content,
			};
		})
		.filter((item): item is { id: string; role: string; content: string } =>
			Boolean(item),
		);
}

function ChatMessage({ message }: { message: WorkbenchMessage }) {
	const isUser = message.role === "user";
	const text = textFromMessage(message);
	const fileParts = message.parts.filter(isFilePart);
	const toolParts = message.parts.filter(isToolPart);

	return (
		<article className={isUser ? "message user-message" : "message ai-message"}>
			<div className="message-avatar" aria-hidden="true">
				{isUser ? <CircleUserRound size={15} /> : <Bot size={15} />}
			</div>
			<div className="message-shell">
				<header className="message-meta">
					<span>{isUser ? "USER" : "AGENT"}</span>
					{!isUser && <CheckCircle2 size={12} />}
				</header>
				{fileParts.length > 0 && (
					<ul className="message-attachments" aria-label="Message attachments">
						{fileParts.map((part) => (
							<li
								className="message-thumbnail"
								key={`${part.url}-${part.filename}`}
							>
								{part.mediaType.startsWith("image/") ? (
									// biome-ignore lint/performance/noImgElement: local object URL previews are user-selected uploads.
									<img alt={part.filename || "attached image"} src={part.url} />
								) : (
									<Paperclip size={15} />
								)}
								{part.filename && <span>{part.filename}</span>}
							</li>
						))}
					</ul>
				)}
				{text && <p>{text}</p>}
				{toolParts.map((part) => (
					<ToolMessage key={`${part.toolCallId}-${part.type}`} part={part} />
				))}
			</div>
		</article>
	);
}

function ComposerAttachmentPreview({
	attachment,
	onRemove,
}: {
	attachment: ComposerAttachment;
	onRemove: (id: string) => void;
}) {
	const isImage = Boolean(attachment.previewUrl);

	return (
		<li className={isImage ? "attachment-preview image" : "attachment-preview"}>
			{isImage ? (
				// biome-ignore lint/performance/noImgElement: local object URL previews are user-selected uploads.
				<img alt={attachment.name} src={attachment.previewUrl || ""} />
			) : (
				<Paperclip size={14} />
			)}
			<span>{attachment.name}</span>
			{attachment.status === "uploading" && <small>uploading</small>}
			<button
				type="button"
				aria-label={`Remove ${attachment.name}`}
				onClick={() => onRemove(attachment.id)}
			>
				<X size={12} />
			</button>
		</li>
	);
}

export const ChatRail = memo(function ChatRail({
	modelOptions,
	selectedModel,
	uploadStatus,
	onModelChange,
	onUploadFiles,
}: ChatRailProps) {
	const [input, setInput] = useState("");
	const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
	const [isDragActive, setIsDragActive] = useState(false);
	const messageStreamRef = useRef<HTMLDivElement>(null);
	const textareaRef = useRef<HTMLTextAreaElement>(null);
	const uploadInputRef = useRef<HTMLInputElement>(null);
	const dragDepthRef = useRef(0);
	const attachmentPreviewUrlsRef = useRef(new Set<string>());
	const shouldStickToBottomRef = useRef(true);
	const transport = useMemo(
		() =>
			new DefaultChatTransport<WorkbenchMessage>({
				api: "/api/chat",
				prepareSendMessagesRequest(request) {
					return {
						body: {
							id: request.id,
							messages: request.messages,
							selectedChatModel: selectedModel,
							...request.body,
						},
					};
				},
			}),
		[selectedModel],
	);
	const { messages, sendMessage, status, stop } = useChat<WorkbenchMessage>({
		id: "data-agentics-workbench",
		messages: initialMessages,
		transport,
	});
	const isBusy = status === "submitted" || status === "streaming";
	const commandText = input.trimStart();
	const commandMenuOpen = commandText.startsWith("/") && !isBusy;
	const commandQuery = commandMenuOpen
		? commandText.slice(1).split(/\s+/, 1)[0].toLowerCase()
		: "";
	const filteredCommands = useMemo(() => {
		if (!commandMenuOpen || !commandQuery) {
			return composerCommands;
		}
		return composerCommands.filter((command) =>
			command.label.toLowerCase().includes(commandQuery),
		);
	}, [commandMenuOpen, commandQuery]);
	const [activeCommandIndex, setActiveCommandIndex] = useState(0);
	const selectedCommandIndex = Math.min(
		activeCommandIndex,
		Math.max(filteredCommands.length - 1, 0),
	);
	const removeAttachment = useCallback((id: string) => {
		setAttachments((currentAttachments) => {
			const removedAttachment = currentAttachments.find(
				(attachment) => attachment.id === id,
			);
			if (removedAttachment) {
				revokeAttachmentPreview(
					removedAttachment,
					attachmentPreviewUrlsRef.current,
				);
			}
			return currentAttachments.filter((attachment) => attachment.id !== id);
		});
	}, []);
	const clearAttachments = useCallback(
		(options?: { keepPreviewUrls?: boolean }) => {
			setAttachments((currentAttachments) => {
				if (!options?.keepPreviewUrls) {
					for (const attachment of currentAttachments) {
						revokeAttachmentPreview(
							attachment,
							attachmentPreviewUrlsRef.current,
						);
					}
				}
				return [];
			});
		},
		[],
	);
	const uploadSelectedFiles = useCallback(
		(files: FileList | File[] | null) => {
			const selectedFiles = Array.from(files || []);
			if (selectedFiles.length === 0) {
				return;
			}
			const nextAttachments = selectedFiles.map((file) => ({
				id: attachmentId(file),
				name: file.name || "pasted-image.png",
				type: file.type,
				previewUrl: attachmentPreview(file),
				status: "uploading" as const,
			}));
			for (const attachment of nextAttachments) {
				if (attachment.previewUrl) {
					attachmentPreviewUrlsRef.current.add(attachment.previewUrl);
				}
			}
			setAttachments((currentAttachments) => [
				...currentAttachments,
				...nextAttachments,
			]);
			void Promise.resolve(onUploadFiles(selectedFiles))
				.then((uploadedFiles) => {
					const uploadedById = new Map(
						nextAttachments.map((attachment, attachmentIndex) => [
							attachment.id,
							uploadedFiles[attachmentIndex],
						]),
					);
					setAttachments((currentAttachments) =>
						currentAttachments.map((attachment) => {
							const uploadedFile = uploadedById.get(attachment.id);
							if (!uploadedById.has(attachment.id)) {
								return attachment;
							}
							return {
								...attachment,
								name: uploadedFile?.name || attachment.name,
								sourcePath: uploadedFile?.path || attachment.sourcePath,
								status: "attached",
							};
						}),
					);
				})
				.catch(() => {
					const uploadedIds = new Set(
						nextAttachments.map((attachment) => attachment.id),
					);
					setAttachments((currentAttachments) =>
						currentAttachments.map((attachment) =>
							uploadedIds.has(attachment.id)
								? { ...attachment, status: "error" }
								: attachment,
						),
					);
				});
		},
		[onUploadFiles],
	);
	const fileInputChanged = useCallback(
		(event: ChangeEvent<HTMLInputElement>) => {
			uploadSelectedFiles(event.target.files);
			event.target.value = "";
		},
		[uploadSelectedFiles],
	);
	const pastedIntoComposer = useCallback(
		(event: ReactClipboardEvent<HTMLTextAreaElement>) => {
			const imageFiles = imageFilesFromClipboard(event);
			if (imageFiles.length === 0) {
				return;
			}
			event.preventDefault();
			uploadSelectedFiles(imageFiles);
		},
		[uploadSelectedFiles],
	);
	const dragEntered = (event: ReactDragEvent<HTMLFormElement>) => {
		if (!hasDraggedFiles(event)) {
			return;
		}
		event.preventDefault();
		dragDepthRef.current += 1;
		setIsDragActive(true);
	};
	const dragLeft = (event: ReactDragEvent<HTMLFormElement>) => {
		if (!hasDraggedFiles(event)) {
			return;
		}
		event.preventDefault();
		dragDepthRef.current = Math.max(0, dragDepthRef.current - 1);
		if (dragDepthRef.current === 0) {
			setIsDragActive(false);
		}
	};
	const dragOver = (event: ReactDragEvent<HTMLFormElement>) => {
		if (!hasDraggedFiles(event)) {
			return;
		}
		event.preventDefault();
		event.dataTransfer.dropEffect = "copy";
	};
	const filesDropped = (event: ReactDragEvent<HTMLFormElement>) => {
		if (!hasDraggedFiles(event)) {
			return;
		}
		event.preventDefault();
		dragDepthRef.current = 0;
		setIsDragActive(false);
		uploadSelectedFiles(event.dataTransfer.files);
	};

	const sendDemoTrace = (mode: DemoTraceMode) => {
		let prompt = input.trim();
		if (commandMenuOpen) {
			const commandBody = commandText.slice(1).trim();
			const firstSpace = commandBody.search(/\s/);
			prompt = firstSpace === -1 ? "" : commandBody.slice(firstSpace).trim();
		}
		const text = prompt || "show all demo trace steps";
		sendMessage(
			{
				role: "user",
				parts: [{ type: "text", text: `@demoTrace ${mode} ${text}` }],
			},
			{ body: { demoTraceMode: mode, selectedChatModel: selectedModel } },
		);
		setInput("");
	};

	useEffect(() => {
		const messageStream = messageStreamRef.current;
		if (!messageStream) {
			return;
		}
		const updateStickiness = () => {
			shouldStickToBottomRef.current = isScrolledNearBottom(messageStream);
		};

		updateStickiness();
		messageStream.addEventListener("scroll", updateStickiness, {
			passive: true,
		});
		return () => {
			messageStream.removeEventListener("scroll", updateStickiness);
		};
	}, []);

	useEffect(() => {
		const messageStream = messageStreamRef.current;
		if (!messageStream || !shouldStickToBottomRef.current) {
			return;
		}

		const animationFrame = requestAnimationFrame(() => {
			messageStream.scrollTop = messageStream.scrollHeight;
			shouldStickToBottomRef.current = true;
		});
		return () => cancelAnimationFrame(animationFrame);
	});

	useLayoutEffect(() => {
		fitComposerTextarea(textareaRef.current);
	});

	useEffect(
		() => () => {
			for (const previewUrl of attachmentPreviewUrlsRef.current) {
				URL.revokeObjectURL(previewUrl);
			}
			attachmentPreviewUrlsRef.current.clear();
		},
		[],
	);

	return (
		<aside className="chat-rail">
			<header className="rail-header">
				<div>
					<span className="eyebrow">CHAT RUNTIME</span>
					<h2>Agent console</h2>
				</div>
				<select
					className="model-select"
					aria-label="Model"
					value={selectedModel}
					onChange={(event) => onModelChange(event.target.value)}
				>
					{modelOptions.map((model) => (
						<option key={model} value={model}>
							{model}
						</option>
					))}
				</select>
			</header>

			<div
				className="message-stream"
				ref={messageStreamRef}
				role="log"
				aria-label="Chat messages"
			>
				{messages.map((message) => (
					<ChatMessage key={message.id} message={message} />
				))}
				{isBusy && (
					<div className="message ai-message thinking">
						<div className="message-avatar" aria-hidden="true">
							<Loader2 size={15} className="spin" />
						</div>
						<div className="message-shell">
							<header className="message-meta">
								<span>AGENT</span>
							</header>
							<p>Dispatching Python workbench call...</p>
						</div>
					</div>
				)}
			</div>

			<form
				className={isDragActive ? "composer drop-active" : "composer"}
				onDragEnter={dragEntered}
				onDragLeave={dragLeft}
				onDragOver={dragOver}
				onDrop={filesDropped}
				onSubmit={(event) => {
					event.preventDefault();
					if (commandMenuOpen) {
						const command = filteredCommands[selectedCommandIndex];
						if (command) {
							sendDemoTrace(command.mode);
						}
						return;
					}
					const text = input.trim();
					const currentSourceFiles = attachedSourceFiles(attachments);
					const currentFileParts = messageFileParts(attachments);
					const messageText = text || attachmentPrompt(attachments);
					if (!messageText) {
						return;
					}
					sendMessage(
						{ text: messageText, files: currentFileParts },
						{
							body: {
								selectedChatModel: selectedModel,
								sourceFiles: currentSourceFiles,
							},
						},
					);
					setInput("");
					clearAttachments({ keepPreviewUrls: true });
				}}
			>
				<input
					ref={uploadInputRef}
					accept={ACCEPTED_UPLOAD_TYPES}
					aria-hidden="true"
					className="sr-only"
					hidden
					multiple
					onChange={fileInputChanged}
					tabIndex={-1}
					type="file"
				/>
				{commandMenuOpen && (
					<section className="command-palette" aria-label="Tool commands">
						<header>
							<span>Tools preview</span>
							<kbd>/</kbd>
						</header>
						<div className="command-options" role="listbox">
							{filteredCommands.length > 0 ? (
								filteredCommands.map((command, commandIndex) => {
									const Icon = command.icon;
									return (
										<button
											type="button"
											className={
												commandIndex === selectedCommandIndex ? "active" : ""
											}
											key={command.id}
											onMouseDown={(event) => event.preventDefault()}
											onClick={() => sendDemoTrace(command.mode)}
											role="option"
											aria-selected={commandIndex === selectedCommandIndex}
										>
											<Icon size={15} />
											<span className="command-copy">
												<span>{command.label}</span>
												<small>{command.description}</small>
											</span>
										</button>
									);
								})
							) : (
								<p>No matching tools</p>
							)}
						</div>
					</section>
				)}
				{attachments.length > 0 && (
					<ul className="composer-attachments" aria-label="Attached files">
						{attachments.map((attachment) => (
							<ComposerAttachmentPreview
								attachment={attachment}
								key={attachment.id}
								onRemove={removeAttachment}
							/>
						))}
					</ul>
				)}
				<button
					className="attachment-button"
					type="button"
					onClick={() => uploadInputRef.current?.click()}
					aria-label="Attach file"
					title={uploadStatus || "Attach file"}
				>
					<Paperclip size={16} />
				</button>
				<textarea
					ref={textareaRef}
					aria-label="Ask the data agent"
					onChange={(event) => {
						setInput(event.target.value);
						setActiveCommandIndex(0);
					}}
					onKeyDown={(event) => {
						if (commandMenuOpen && filteredCommands.length > 0) {
							if (event.key === "ArrowDown") {
								event.preventDefault();
								setActiveCommandIndex(
									(index) => (index + 1) % filteredCommands.length,
								);
								return;
							}
							if (event.key === "ArrowUp") {
								event.preventDefault();
								setActiveCommandIndex(
									(index) =>
										(index - 1 + filteredCommands.length) %
										filteredCommands.length,
								);
								return;
							}
							if (event.key === "Escape") {
								event.preventDefault();
								setInput("");
								return;
							}
						}
						if (event.key === "Enter" && !event.shiftKey) {
							event.preventDefault();
							event.currentTarget.form?.requestSubmit();
						}
					}}
					onPaste={pastedIntoComposer}
					placeholder="Ask about the active data workspace..."
					value={input}
				/>
				<button
					className="send-button"
					type={isBusy ? "button" : "submit"}
					onClick={isBusy ? stop : undefined}
					aria-label={isBusy ? "Stop generation" : "Send message"}
				>
					{isBusy ? <Loader2 className="spin" size={16} /> : <Send size={16} />}
				</button>
				{uploadStatus && attachments.length === 0 ? (
					<span className="composer-status">{uploadStatus}</span>
				) : null}
			</form>
		</aside>
	);
});
