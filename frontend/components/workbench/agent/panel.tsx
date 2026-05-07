"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type FileUIPart } from "ai";
import {
	CheckCircle2,
	ChevronDown,
	CircleUserRound,
	FileText,
	Folder,
	Loader2,
	PanelRight,
	Paperclip,
	Send,
	TriangleAlert,
	Wrench,
	X,
} from "lucide-react";
import type {
	ChangeEvent,
	ClipboardEvent as ReactClipboardEvent,
	DragEvent as ReactDragEvent,
	ReactElement,
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
import { FaRobot } from "react-icons/fa";
import type { UploadedWorkspaceFile } from "@/components/workbench/types";
import type { SkillEntry, SourceFile, Target } from "@/lib/api";
import type { WorkbenchMessage } from "@/lib/chat-contracts";
import {
	buildCommandIndex,
	type CommandIndexItem,
	commandDetailForItem,
	filterCommandIndex,
	filterIndexedItems,
} from "./command-index";

type WorkbenchPart = WorkbenchMessage["parts"][number];
type WorkbenchToolPart = Extract<WorkbenchPart, { toolCallId: string }>;
type WorkbenchFilePart = Extract<WorkbenchPart, { type: "file" }>;
type DemoTraceMode = "fast" | "slow";
type TraceEntry = {
	id: string;
	name: string;
	status: string;
	summary: string;
	depth?: number;
};
type TraceArtifact = {
	tool_trace?: unknown;
	stage_trace?: unknown;
	conversation_trace?: unknown;
};
type ComposerAttachment = {
	id: string;
	name: string;
	type: string;
	previewUrl: string | null;
	sourcePath?: string;
	status: "uploading" | "attached" | "error";
};
type ComposerSourceMention = {
	id: string;
	name: string;
	reference: string;
	detail: string;
	kind: "directory" | "source" | "skill";
};
type AgentPanelProps = {
	bootstrapSourceFiles: SourceFile[];
	isCollapsed: boolean;
	modelOptions: string[];
	selectedModel: string;
	skills: SkillEntry[];
	targets: Target[];
	uploadStatus: string;
	onModelChange: (model: string) => void;
	onRunSql: () => void;
	onChatSettled: () => void;
	onToggle: () => void;
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
				text: "Data workbench online. Ask about loaded files, SQL targets, or the current result set.",
			},
		],
	},
];

const CHAT_BOTTOM_THRESHOLD = 28;
const ACCEPTED_UPLOAD_TYPES = ".csv,.xlsx,.pdf,image/*";
const TEXTAREA_MAX_HEIGHT_VAR = "--composer-textarea-max-height";
const DEFAULT_TEXTAREA_MAX_HEIGHT = 150;
const SOURCE_MENTION_TOKEN_PATTERN = /@([^\s@]+)/g;

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

function attachmentReferenceText(attachments: ComposerAttachment[]) {
	const references = attachedSourceFiles(attachments);
	if (references.length === 0) {
		return "";
	}
	return `Attached files:\n${references.map((reference) => `- ${reference}`).join("\n")}`;
}

function sourceMentionReferenceText(mentions: ComposerSourceMention[]) {
	if (mentions.length === 0) {
		return "";
	}
	return `Referenced paths:\n${mentions.map((mention) => `- ${mention.reference}`).join("\n")}`;
}

function messageTextWithFileContext(
	text: string,
	attachments: ComposerAttachment[],
	sourceMentions: ComposerSourceMention[],
) {
	return [
		text,
		attachmentReferenceText(attachments),
		sourceMentionReferenceText(sourceMentions),
	]
		.filter(Boolean)
		.join("\n\n");
}

function attachedSourceFiles(attachments: ComposerAttachment[]) {
	return attachments
		.filter((attachment) => attachment.status === "attached")
		.map((attachment) => attachment.sourcePath || attachment.name)
		.filter(Boolean);
}

function uniqueStrings(values: string[]) {
	return Array.from(new Set(values.filter(Boolean)));
}

function sourceFileReference(source: SourceFile) {
	return source.source_path || source.destination_path || source.name;
}

function sourceFileMention(source: SourceFile): ComposerSourceMention {
	const reference = sourceFileReference(source);
	return {
		id: source.id || reference,
		name: source.name,
		reference,
		detail: pathDirectory(reference),
		kind: "source",
	};
}

function skillFileMentions(skills: SkillEntry[]): ComposerSourceMention[] {
	return skills.flatMap((skill) => {
		const mentions: ComposerSourceMention[] = [];
		const instructionPath =
			skill.instructions?.relative_path ||
			skill.instructions?.path ||
			skill.path ||
			skill.skills_path ||
			"";
		if (instructionPath) {
			mentions.push({
				id: `skill-file-${skill.name}-${instructionPath}`,
				name: skillFileLabel(instructionPath, "SKILL.md"),
				reference: instructionPath,
				detail: pathDirectory(instructionPath),
				kind: "skill",
			});
		}
		for (const group of ["examples", "references", "scripts"] as const) {
			for (const resource of skill[group] || []) {
				const path = resource.relative_path || resource.path || "";
				if (!path) {
					continue;
				}
				mentions.push({
					id: `skill-file-${skill.name}-${path}`,
					name: skillFileLabel(path, group),
					reference: path,
					detail: pathDirectory(path),
					kind: "skill",
				});
			}
		}
		return mentions;
	});
}

function directoryMentions(mentions: ComposerSourceMention[]) {
	const directories = new Map<string, ComposerSourceMention>();
	for (const mention of mentions) {
		for (const directory of pathDirectories(mention.reference)) {
			if (directories.has(directory)) {
				continue;
			}
			directories.set(directory, {
				id: `directory-${directory}`,
				name: pathBasename(directory),
				reference: directory,
				detail: pathDirectory(directory),
				kind: "directory",
			});
		}
	}
	return Array.from(directories.values());
}

function indexedPathMentions(sourceFiles: SourceFile[], skills: SkillEntry[]) {
	const fileMentions = [
		...sourceFiles.map(sourceFileMention),
		...skillFileMentions(skills),
	];
	return [...directoryMentions(fileMentions), ...fileMentions];
}

function skillFileLabel(path: string, fallback: string) {
	return pathBasename(path) || fallback;
}

function pathDirectory(path: string) {
	return path.split("/").filter(Boolean).slice(0, -1).join("/");
}

function pathBasename(path: string) {
	return path.split("/").filter(Boolean).at(-1) || path;
}

function pathDirectories(path: string) {
	const parts = path.split("/").filter(Boolean).slice(0, -1);
	return parts.map((_, index) => parts.slice(0, index + 1).join("/"));
}

function sourceMentionReferences(mentions: ComposerSourceMention[]) {
	return mentions
		.filter((mention) => mention.kind === "source")
		.map((mention) => mention.reference);
}

function sourceMentionsInText(text: string, mentions: ComposerSourceMention[]) {
	const tokenNames = sourceMentionTokenNames(text);
	return mentions.filter((mention) =>
		tokenNames.has(sourceMentionName(mention)),
	);
}

function sourceMentionTokenNames(text: string) {
	return new Set(
		Array.from(text.matchAll(SOURCE_MENTION_TOKEN_PATTERN), (match) =>
			match[1].toLowerCase(),
		),
	);
}

function sourceMentionName(mention: ComposerSourceMention) {
	return mention.name.toLowerCase();
}

function activeFileMentionTrigger(text: string) {
	const match = /(?:^|\s)@([^@]*)$/.exec(text);
	if (!match) {
		return null;
	}
	return {
		query: match[1],
		start: match.index + match[0].lastIndexOf("@"),
	};
}

function isCompletedFileMentionTrigger(
	query: string,
	mentions: ComposerSourceMention[],
) {
	const trimmedQuery = query.trim().toLowerCase();
	return (
		query.endsWith(" ") &&
		mentions.some((mention) => sourceMentionName(mention) === trimmedQuery)
	);
}

function replaceActiveFileMention(
	text: string,
	mention: ComposerSourceMention,
) {
	const trigger = activeFileMentionTrigger(text);
	const token = `@${mention.name} `;
	if (!trigger) {
		return `${text}${text.endsWith(" ") || !text ? "" : " "}${token}`;
	}
	return `${text.slice(0, trigger.start)}${token}`;
}

function removeSourceMentionToken(
	text: string,
	mention: ComposerSourceMention,
) {
	const tokenPattern = new RegExp(`@${escapeRegExp(mention.name)}\\s?`, "gi");
	return text.replace(tokenPattern, "").replace(/\s{2,}/g, " ");
}

function escapeRegExp(value: string) {
	return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function composerHighlightParts(
	text: string,
	sourceMentions: ComposerSourceMention[],
) {
	const parts: ReactElement[] = [];
	const selectedMentionNames = new Set(
		sourceMentions.map((mention) => sourceMentionName(mention)),
	);
	const mentionPattern = new RegExp(SOURCE_MENTION_TOKEN_PATTERN);
	let cursor = 0;
	let match = mentionPattern.exec(text);
	while (match) {
		if (match.index > cursor) {
			parts.push(
				<span key={`text-${cursor}`}>{text.slice(cursor, match.index)}</span>,
			);
		}
		const mentionName = match[0].slice(1).toLowerCase();
		const className = selectedMentionNames.has(mentionName)
			? "composer-mention-token"
			: undefined;
		parts.push(
			<span className={className} key={`mention-${match.index}`}>
				{match[0]}
			</span>,
		);
		cursor = match.index + match[0].length;
		match = mentionPattern.exec(text);
	}
	if (cursor < text.length) {
		parts.push(<span key={`text-${cursor}`}>{text.slice(cursor)}</span>);
	}
	return parts;
}

function messageFileParts(attachments: ComposerAttachment[]): FileUIPart[] {
	return attachments
		.filter((attachment) => attachment.status === "attached")
		.map((attachment) => ({
			type: "file",
			mediaType: attachment.type || "application/octet-stream",
			filename: attachment.name,
			url: attachment.previewUrl || attachment.sourcePath || attachment.name,
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

function inputToolName(input: unknown) {
	if (!input || typeof input !== "object") {
		return null;
	}
	const tool = (input as { tool?: unknown }).tool;
	return typeof tool === "string" && tool.trim() ? tool : null;
}

function traceDetail(item: TraceEntry) {
	return item.summary ? `${item.status} · ${item.summary}` : item.status;
}

function toolHeading(
	part: WorkbenchToolPart,
	trace: TraceEntry[],
	input: unknown,
) {
	const [item] = trace;
	if (item) {
		return {
			title: item.name,
			detail: traceDetail(item),
		};
	}
	return {
		title: inputToolName(input) || toolTitle(part),
		detail: "No backend tools",
	};
}

function ToolCard({
	title,
	detail,
	bodyText,
	isError,
	state,
	trace = [],
	conversation = [],
	errorText,
	input,
	output,
	showEmptyTrace = false,
}: {
	title: string;
	detail: string;
	bodyText?: string;
	isError: boolean;
	state: string;
	trace?: TraceEntry[];
	conversation?: Array<{ id: string; role: string; content: string }>;
	errorText?: string;
	input?: unknown;
	output?: unknown;
	showEmptyTrace?: boolean;
}) {
	return (
		<details className="tool-message">
			<summary>
				<span className="tool-message-icon">
					{isError ? <TriangleAlert size={13} /> : <Wrench size={13} />}
				</span>
				<span className="tool-summary-main">
					<span className="tool-summary-name">{title}</span>
					<span className="tool-summary-steps">{detail}</span>
				</span>
				{isError && (
					<span className="tool-state error">{state.replaceAll("-", " ")}</span>
				)}
				<ChevronDown size={13} className="tool-chevron" />
			</summary>
			<div className="tool-message-body">
				{bodyText && <p className="tool-body-text">{bodyText}</p>}
				{trace.length > 0 && (
					<section className="backend-trace">
						{trace.map((item) => (
							<div
								className="trace-row"
								data-depth={item.depth || 0}
								key={item.id}
							>
								<span>{item.name}</span>
								<p>
									<b>{item.status}</b>
									{item.summary ? ` · ${item.summary}` : ""}
								</p>
							</div>
						))}
					</section>
				)}
				{trace.length === 0 && showEmptyTrace && !isError && (
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

function ToolMessage({ part }: { part: WorkbenchToolPart }) {
	const input = "input" in part ? part.input : undefined;
	const output = "output" in part ? part.output : undefined;
	const errorText = "errorText" in part ? part.errorText : undefined;
	const partRecord = part as { state?: unknown; type: string };
	const state =
		typeof partRecord.state === "string" ? partRecord.state : partRecord.type;
	const isError =
		state === "output-error" || partRecord.type === "tool-output-error";
	const artifact = artifactFromOutput(output);
	const trace = traceItems(artifact?.tool_trace);
	const heading = toolHeading(part, trace, input);
	const conversation = conversationTrace(artifact);
	const stageTrace = traceItems(artifact?.stage_trace);

	if (stageTrace.length > 0) {
		return (
			<>
				{stageTrace.map((item, itemIndex) => (
					<ToolCard
						bodyText={item.summary || traceDetail(item)}
						conversation={itemIndex === 0 ? conversation : []}
						detail={traceDetail(item)}
						input={itemIndex === 0 ? input : undefined}
						isError={isError}
						key={`${part.toolCallId}-${item.id}`}
						output={itemIndex === 0 ? output : undefined}
						state={state}
						title={item.name}
						trace={[]}
					/>
				))}
			</>
		);
	}

	return (
		<ToolCard
			conversation={conversation}
			detail={heading.detail}
			errorText={errorText}
			input={input}
			isError={isError}
			output={output}
			showEmptyTrace
			state={state}
			title={heading.title}
			trace={trace}
		/>
	);
}

function artifactFromOutput(output: unknown) {
	if (!output || typeof output !== "object") {
		return null;
	}
	const artifact = (output as { artifact?: unknown }).artifact;
	return artifact && typeof artifact === "object"
		? (artifact as TraceArtifact)
		: null;
}

function traceItems(value: unknown): TraceEntry[] {
	if (!Array.isArray(value)) {
		return [];
	}
	return value
		.map((item): TraceEntry | null => {
			if (!item || typeof item !== "object") {
				return null;
			}
			const candidate = item as {
				id?: unknown;
				name?: unknown;
				status?: unknown;
				summary?: unknown;
				depth?: unknown;
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
				depth:
					typeof candidate.depth === "number" ? candidate.depth : undefined,
			};
		})
		.filter((item): item is TraceEntry => item !== null);
}

function conversationTrace(
	artifact: TraceArtifact | null,
): Array<{ id: string; role: string; content: string }> {
	if (!artifact) {
		return [];
	}
	const trace = artifact.conversation_trace;
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
	const fileParts = message.parts.filter(isFilePart);
	const renderedParts = message.parts
		.map((part) => {
			if (part.type === "text" && part.text) {
				return <p key={`${message.id}-text-${part.text}`}>{part.text}</p>;
			}
			if (isToolPart(part)) {
				return (
					<ToolMessage
						key={`${message.id}-tool-${part.toolCallId}-${part.type}`}
						part={part}
					/>
				);
			}
			return null;
		})
		.filter((item): item is ReactElement => item !== null);

	return (
		<article className={isUser ? "message user-message" : "message ai-message"}>
			<div className="message-avatar" aria-hidden="true">
				{isUser ? (
					<CircleUserRound size={15} />
				) : (
					<FaRobot aria-hidden="true" className="agent-icon" size={15} />
				)}
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
				{renderedParts}
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

function ComposerSourceMentionChip({
	mention,
	onRemove,
}: {
	mention: ComposerSourceMention;
	onRemove: (mention: ComposerSourceMention) => void;
}) {
	return (
		<li className="attachment-preview source-mention">
			{mention.kind === "directory" ? (
				<Folder size={14} />
			) : (
				<FileText size={14} />
			)}
			<span>{mention.name}</span>
			<button
				type="button"
				aria-label={`Remove ${mention.name}`}
				onClick={() => onRemove(mention)}
			>
				<X size={12} />
			</button>
		</li>
	);
}

export const AgentPanel = memo(function AgentPanel({
	bootstrapSourceFiles,
	isCollapsed,
	modelOptions,
	onRunSql,
	selectedModel,
	skills,
	targets,
	uploadStatus,
	onModelChange,
	onChatSettled,
	onToggle,
	onUploadFiles,
}: AgentPanelProps) {
	const [input, setInput] = useState("");
	const [attachments, setAttachments] = useState<ComposerAttachment[]>([]);
	const [sourceMentions, setSourceMentions] = useState<ComposerSourceMention[]>(
		[],
	);
	const [isDragActive, setIsDragActive] = useState(false);
	const messageStreamRef = useRef<HTMLDivElement>(null);
	const textareaRef = useRef<HTMLTextAreaElement>(null);
	const highlightLayerRef = useRef<HTMLDivElement>(null);
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
	const previousStatusRef = useRef(status);
	const isBusy = status === "submitted" || status === "streaming";

	useEffect(() => {
		const wasBusy =
			previousStatusRef.current === "submitted" ||
			previousStatusRef.current === "streaming";
		if (wasBusy && !isBusy) {
			onChatSettled();
		}
		previousStatusRef.current = status;
	}, [isBusy, onChatSettled, status]);
	const commandText = input.trimStart();
	const commandMenuOpen = commandText.startsWith("/") && !isBusy;
	const commandQuery = commandMenuOpen ? commandText.slice(1) : "";
	const fileMentionTrigger = !commandMenuOpen
		? activeFileMentionTrigger(input)
		: null;
	const fileMentionMenuOpen =
		Boolean(fileMentionTrigger) &&
		!isBusy &&
		!isCompletedFileMentionTrigger(
			fileMentionTrigger?.query || "",
			sourceMentions,
		);
	const sourceMentionItems = useMemo(
		() => indexedPathMentions(bootstrapSourceFiles, skills),
		[bootstrapSourceFiles, skills],
	);
	const filteredSourceMentions = useMemo(() => {
		if (!fileMentionMenuOpen) {
			return [];
		}
		return filterIndexedItems(
			sourceMentionItems,
			fileMentionTrigger?.query || "",
			(mention) => [
				{ value: mention.name, weight: 8 },
				{ value: mention.reference, weight: 5 },
				{ value: mention.detail, weight: 3 },
				{ value: mention.kind, weight: 1 },
			],
		);
	}, [fileMentionMenuOpen, fileMentionTrigger?.query, sourceMentionItems]);
	const commandIndex = useMemo(
		() =>
			buildCommandIndex({
				sourceFiles: bootstrapSourceFiles,
				targets,
				skills,
			}),
		[bootstrapSourceFiles, skills, targets],
	);
	const filteredCommands = useMemo(() => {
		if (!commandMenuOpen) {
			return [];
		}
		return filterCommandIndex(commandIndex, commandQuery);
	}, [commandIndex, commandMenuOpen, commandQuery]);
	const [activeCommandIndex, setActiveCommandIndex] = useState(0);
	const [activeFileMentionIndex, setActiveFileMentionIndex] = useState(0);
	const selectedCommandIndex = Math.min(
		activeCommandIndex,
		Math.max(filteredCommands.length - 1, 0),
	);
	const selectedFileMentionIndex = Math.min(
		activeFileMentionIndex,
		Math.max(filteredSourceMentions.length - 1, 0),
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
	const syncSourceMentions = useCallback(
		(nextInput: string, selectedMention?: ComposerSourceMention) => {
			setSourceMentions((currentMentions) => {
				const nextMentions =
					selectedMention &&
					!currentMentions.some(
						(currentMention) => currentMention.id === selectedMention.id,
					)
						? [...currentMentions, selectedMention]
						: currentMentions;
				return sourceMentionsInText(nextInput, nextMentions);
			});
		},
		[],
	);
	const setComposerInput = useCallback(
		(nextInput: string) => {
			setInput(nextInput);
			syncSourceMentions(nextInput);
		},
		[syncSourceMentions],
	);
	const focusComposerTextarea = useCallback(() => {
		requestAnimationFrame(() => textareaRef.current?.focus());
	}, []);
	const selectSourceMention = useCallback(
		(mention: ComposerSourceMention) => {
			setInput((currentInput) => {
				const nextInput = replaceActiveFileMention(currentInput, mention);
				syncSourceMentions(nextInput, mention);
				return nextInput;
			});
			setActiveFileMentionIndex(0);
			focusComposerTextarea();
		},
		[focusComposerTextarea, syncSourceMentions],
	);
	const removeSourceMention = useCallback(
		(mention: ComposerSourceMention) => {
			setInput((currentInput) => {
				const nextInput = removeSourceMentionToken(currentInput, mention);
				syncSourceMentions(nextInput);
				return nextInput;
			});
			focusComposerTextarea();
		},
		[focusComposerTextarea, syncSourceMentions],
	);
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

	const sendDemoTrace = (mode: DemoTraceMode, promptOverride = "") => {
		const prompt = promptOverride || input.trim();
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

	const submitCommandItem = (item: CommandIndexItem) => {
		const detail = commandDetailForItem(item, commandText);
		if (item.action === "run-sql") {
			onRunSql();
			setInput("");
			return;
		}
		if (item.action === "demo-trace" && item.demoMode) {
			sendDemoTrace(item.demoMode, detail);
			return;
		}
		const prompt = item.prompt?.(detail) || item.label;
		const currentSourceFiles = uniqueStrings([
			...(item.sourceFiles || []),
			...sourceMentionReferences(sourceMentions),
			...attachedSourceFiles(attachments),
		]);
		const currentFileParts = messageFileParts(attachments);
		const messageText = messageTextWithFileContext(
			prompt,
			attachments,
			sourceMentions,
		);
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
		setSourceMentions([]);
		clearAttachments({ keepPreviewUrls: true });
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
		<aside className={isCollapsed ? "chat-rail collapsed" : "chat-rail"}>
			<header className="rail-header">
				<div className="rail-title">
					<FaRobot aria-hidden="true" className="agent-icon" size={14} />
					<span>Agent</span>
				</div>
				<div className="rail-actions">
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
					<button
						className="panel-toggle"
						type="button"
						aria-label="Collapse agent panel"
						aria-expanded={!isCollapsed}
						onClick={onToggle}
					>
						<PanelRight size={14} />
					</button>
				</div>
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
					<output className="stream-status" aria-live="polite">
						<Loader2 size={14} className="spin" aria-hidden="true" />
						<span>Dispatching Python workbench call...</span>
					</output>
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
							submitCommandItem(command);
						}
						return;
					}
					if (fileMentionMenuOpen) {
						const sourceMention =
							filteredSourceMentions[selectedFileMentionIndex];
						if (sourceMention) {
							selectSourceMention(sourceMention);
						}
						return;
					}
					const text = input.trim();
					const currentSourceFiles = uniqueStrings([
						...sourceMentionReferences(sourceMentions),
						...attachedSourceFiles(attachments),
					]);
					const currentFileParts = messageFileParts(attachments);
					const messageText =
						messageTextWithFileContext(text, attachments, sourceMentions) ||
						attachmentPrompt(attachments);
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
					setSourceMentions([]);
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
					<section className="command-palette" aria-label="Command index">
						<header>
							<span>Command index</span>
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
											onClick={() => submitCommandItem(command)}
											role="option"
											aria-selected={commandIndex === selectedCommandIndex}
										>
											<Icon size={15} />
											<span className="command-copy">
												<span>
													<span>{command.label}</span>
													<b>{command.category}</b>
												</span>
												<small>{command.description}</small>
											</span>
										</button>
									);
								})
							) : (
								<p>No matching commands</p>
							)}
						</div>
					</section>
				)}
				{fileMentionMenuOpen && (
					<section className="command-palette" aria-label="File index">
						<header>
							<span>File index</span>
							<kbd>@</kbd>
						</header>
						<div className="command-options" role="listbox">
							{filteredSourceMentions.length > 0 ? (
								<>
									<span className="command-section-label">Files</span>
									{filteredSourceMentions.map((mention, mentionIndex) => (
										<button
											type="button"
											className={
												mentionIndex === selectedFileMentionIndex
													? "active file-option"
													: "file-option"
											}
											key={mention.id}
											onMouseDown={(event) => event.preventDefault()}
											onClick={() => selectSourceMention(mention)}
											role="option"
											aria-selected={mentionIndex === selectedFileMentionIndex}
										>
											{mention.kind === "directory" ? (
												<Folder size={15} />
											) : (
												<FileText size={15} />
											)}
											<span className="file-option-copy">
												<span>{mention.name}</span>
												{mention.detail && <small>{mention.detail}</small>}
											</span>
										</button>
									))}
								</>
							) : (
								<p>No matching files</p>
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
				{sourceMentions.length > 0 && (
					<ul className="composer-attachments" aria-label="Referenced files">
						{sourceMentions.map((mention) => (
							<ComposerSourceMentionChip
								key={mention.id}
								mention={mention}
								onRemove={removeSourceMention}
							/>
						))}
					</ul>
				)}
				<div className="composer-input-wrap">
					<div
						aria-hidden="true"
						className="composer-highlight-layer"
						ref={highlightLayerRef}
					>
						{composerHighlightParts(input, sourceMentions)}
					</div>
					<textarea
						ref={textareaRef}
						aria-label="Ask the data agent"
						onChange={(event) => {
							const nextInput = event.target.value;
							setComposerInput(nextInput);
							setActiveCommandIndex(0);
							setActiveFileMentionIndex(0);
						}}
						onKeyDown={(event) => {
							if (commandMenuOpen && filteredCommands.length > 0) {
								if (event.key === "Tab") {
									event.preventDefault();
									const command = filteredCommands[selectedCommandIndex];
									if (command) {
										submitCommandItem(command);
									}
									return;
								}
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
							if (fileMentionMenuOpen && filteredSourceMentions.length > 0) {
								if (event.key === "Tab") {
									event.preventDefault();
									const sourceMention =
										filteredSourceMentions[selectedFileMentionIndex];
									if (sourceMention) {
										selectSourceMention(sourceMention);
									}
									return;
								}
								if (event.key === "ArrowDown") {
									event.preventDefault();
									setActiveFileMentionIndex(
										(index) => (index + 1) % filteredSourceMentions.length,
									);
									return;
								}
								if (event.key === "ArrowUp") {
									event.preventDefault();
									setActiveFileMentionIndex(
										(index) =>
											(index - 1 + filteredSourceMentions.length) %
											filteredSourceMentions.length,
									);
									return;
								}
								if (event.key === "Escape") {
									event.preventDefault();
									setInput((currentInput) => {
										const trigger = activeFileMentionTrigger(currentInput);
										return trigger
											? currentInput.slice(0, trigger.start)
											: currentInput;
									});
									return;
								}
							}
							if (event.key === "Enter" && !event.shiftKey) {
								event.preventDefault();
								event.currentTarget.form?.requestSubmit();
							}
						}}
						onPaste={pastedIntoComposer}
						onScroll={(event) => {
							if (highlightLayerRef.current) {
								highlightLayerRef.current.scrollTop =
									event.currentTarget.scrollTop;
							}
						}}
						placeholder="Ask agent to do things"
						value={input}
					/>
				</div>
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
