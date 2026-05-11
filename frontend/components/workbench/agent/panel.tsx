"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport, type FileUIPart } from "ai";
import {
	CheckCircle2,
	ChevronDown,
	CircleUserRound,
	Database,
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
	Fragment,
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
import type { SkillEntry, SourceFile, SqlArtifact } from "@/lib/api";
import type { WorkbenchMessage } from "@/lib/chat-contracts";
import { ChatMarkdown } from "./chat-markdown";
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
	kind: "artifact" | "directory" | "source" | "skill" | "sqlArtifact";
	token: string;
};
type AgentPanelProps = {
	bootstrapSourceFiles: SourceFile[];
	isCollapsed: boolean;
	modelOptions: string[];
	selectedModel: string;
	skills: SkillEntry[];
	sqlArtifacts: SqlArtifact[];
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
				text: "Data workbench online. Ask about loaded files, SQL artifacts, or the current result set.",
			},
		],
	},
];

const CHAT_BOTTOM_THRESHOLD = 28;
const ACCEPTED_UPLOAD_TYPES = ".csv,.xlsx,.pdf,image/*";
const TEXTAREA_MAX_HEIGHT_VAR = "--composer-textarea-max-height";
const DEFAULT_TEXTAREA_MAX_HEIGHT = 150;
const SOURCE_MENTION_TOKEN_PATTERN = /@([^\s@]+)/g;
const COMMAND_CATEGORY_ORDER: CommandIndexItem["category"][] = [
	"command",
	"skill",
];
const COMMAND_CATEGORY_LABELS: Record<CommandIndexItem["category"], string> = {
	command: "Commands",
	skill: "Skills",
};

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

function attachedSourceFiles(attachments: ComposerAttachment[]) {
	return attachments
		.filter((attachment) => attachment.status === "attached")
		.map((attachment) => attachment.sourcePath || attachment.name)
		.filter(Boolean);
}

function uniqueStrings(values: string[]) {
	return Array.from(new Set(values.filter(Boolean)));
}

function commandSections(commands: CommandIndexItem[]) {
	return COMMAND_CATEGORY_ORDER.map((category) => ({
		category,
		items: commands
			.map((command, index) => ({ command, index }))
			.filter((item) => item.command.category === category),
	})).filter((section) => section.items.length > 0);
}

function sourceMentionSections(mentions: ComposerSourceMention[]) {
	const fileItems = mentions
		.map((mention, index) => ({ mention, index }))
		.filter((item) => !isArtifactMention(item.mention));
	const artifactItems = mentions
		.map((mention, index) => ({ mention, index }))
		.filter((item) => isArtifactMention(item.mention));
	return [
		{ label: "Files", items: fileItems },
		{ label: "Artifacts", items: artifactItems },
	].filter((section) => section.items.length > 0);
}

function sourceFileReference(source: SourceFile) {
	return source.source_path || source.destination_path || source.name;
}

function sourceFileMention(source: SourceFile): ComposerSourceMention {
	const reference = sourceFileReference(source);
	const artifactKind = artifactMentionKind(reference || source.name);
	const targetCount = source.target_count ?? source.targets?.length ?? 0;
	let detail = pathDirectory(reference);
	if (targetCount > 0) {
		detail = `${targetCount} extracted target${targetCount === 1 ? "" : "s"}`;
	} else if (artifactKind) {
		detail = artifactMentionDetail(reference || source.name);
	}
	return {
		id: source.id || reference,
		name: source.name,
		reference,
		detail,
		kind: artifactKind ? "artifact" : "source",
		token: source.name,
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
				token: skillFileLabel(instructionPath, "SKILL.md"),
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
					detail: artifactMentionDetail(path) || pathDirectory(path),
					kind: artifactMentionKind(path) ? "artifact" : "skill",
					token: skillFileLabel(path, group),
				});
			}
		}
		return mentions;
	});
}

function sqlArtifactMentions(
	sqlArtifacts: SqlArtifact[],
): ComposerSourceMention[] {
	return sqlArtifacts.map((sqlArtifact) => ({
		id: `sql-artifact-${sqlArtifact.name}`,
		name: sqlArtifact.name,
		reference: sqlArtifact.name,
		detail: sqlArtifactDetail(sqlArtifact),
		kind: "sqlArtifact",
		token: sqlArtifact.name,
	}));
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
				token: pathBasename(directory),
			});
		}
	}
	return Array.from(directories.values());
}

function indexedPathMentions(
	sourceFiles: SourceFile[],
	skills: SkillEntry[],
	sqlArtifacts: SqlArtifact[],
) {
	const fileMentions = [
		...sourceFiles.map(sourceFileMention),
		...skillFileMentions(skills),
	];
	return disambiguatedSourceMentionTokens([
		...directoryMentions(fileMentions),
		...fileMentions,
		...sqlArtifactMentions(sqlArtifacts),
	]);
}

function disambiguatedSourceMentionTokens(mentions: ComposerSourceMention[]) {
	const tokenCounts = new Map<string, number>();
	for (const mention of mentions) {
		const token = sourceMentionToken(mention);
		tokenCounts.set(token, (tokenCounts.get(token) || 0) + 1);
	}
	return mentions.map((mention) => {
		const token = sourceMentionToken(mention);
		if (tokenCounts.get(token) === 1) {
			return mention;
		}
		return {
			...mention,
			token: mention.reference || mention.id,
		};
	});
}

function skillFileLabel(path: string, fallback: string) {
	return pathBasename(path) || fallback;
}

function sqlArtifactDetail(sqlArtifact: SqlArtifact) {
	const noun =
		sqlArtifact.type === "view" || sqlArtifact.kind.includes("view")
			? "view"
			: "table";
	if (sqlArtifact.row_count == null) {
		return `${noun} / unknown size`;
	}
	if (sqlArtifact.column_count != null) {
		return `${noun} / ${sqlArtifact.row_count} x ${sqlArtifact.column_count}`;
	}
	return `${noun} / ${sqlArtifact.row_count} rows`;
}

function artifactMentionKind(path: string) {
	const extension = pathExtension(path);
	if (extension === "sql") {
		return "SQL file";
	}
	if (extension === "sqlite" || extension === "db") {
		return "SQLite database";
	}
	return "";
}

function artifactMentionDetail(path: string) {
	const kind = artifactMentionKind(path);
	const directory = pathDirectory(path);
	if (!kind) {
		return "";
	}
	return directory ? `${kind} / ${directory}` : kind;
}

function isArtifactMention(mention: ComposerSourceMention) {
	return mention.kind === "artifact" || mention.kind === "sqlArtifact";
}

function pathDirectory(path: string) {
	return path.split("/").filter(Boolean).slice(0, -1).join("/");
}

function pathBasename(path: string) {
	return path.split("/").filter(Boolean).at(-1) || path;
}

function pathExtension(path: string) {
	const basename = pathBasename(path).toLowerCase();
	const extension = basename.split(".").at(-1) || "";
	return extension === basename ? "" : extension;
}

function pathDirectories(path: string) {
	const parts = path.split("/").filter(Boolean).slice(0, -1);
	return parts.map((_, index) => parts.slice(0, index + 1).join("/"));
}

function sourceMentionReferences(mentions: ComposerSourceMention[]) {
	return mentions
		.filter(
			(mention) => mention.kind === "source" || isArtifactMention(mention),
		)
		.map((mention) => mention.reference);
}

function sourceMentionsInText(text: string, mentions: ComposerSourceMention[]) {
	const tokenNames = sourceMentionTokenNames(text);
	return mentions.filter((mention) =>
		tokenNames.has(sourceMentionToken(mention)),
	);
}

function sourceMentionTokenNames(text: string) {
	return new Set(
		Array.from(text.matchAll(SOURCE_MENTION_TOKEN_PATTERN), (match) =>
			match[1].toLowerCase(),
		),
	);
}

function sourceMentionToken(mention: ComposerSourceMention) {
	return mention.token.toLowerCase();
}

function activeFileMentionTrigger(
	text: string,
	mentions: ComposerSourceMention[] = [],
) {
	const match = /(?:^|\s)@([^@]*)$/.exec(text);
	if (!match) {
		return null;
	}
	if (isCompletedFileMentionTrigger(match[1], mentions)) {
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
	const selectedTokens = new Set(
		mentions.map((mention) => sourceMentionToken(mention)),
	);
	if (query.endsWith(" ") && selectedTokens.has(trimmedQuery)) {
		return true;
	}
	const [firstToken, ...remainingTokens] = trimmedQuery.split(/\s+/);
	return remainingTokens.length > 0 && selectedTokens.has(firstToken);
}

function replaceActiveFileMention(
	text: string,
	mention: ComposerSourceMention,
) {
	const trigger = activeFileMentionTrigger(text);
	const token = `@${mention.token} `;
	if (!trigger) {
		return `${text}${text.endsWith(" ") || !text ? "" : " "}${token}`;
	}
	return `${text.slice(0, trigger.start)}${token}`;
}

function removeSourceMentionToken(
	text: string,
	mention: ComposerSourceMention,
) {
	const tokenPattern = new RegExp(`@${escapeRegExp(mention.token)}\\s?`, "gi");
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
		sourceMentions.map((mention) => sourceMentionToken(mention)),
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
		const mentionToken = match[0].slice(1).toLowerCase();
		const className = selectedMentionNames.has(mentionToken)
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
				return (
					<ChatMarkdown
						content={part.text}
						key={`${message.id}-text-${part.text}`}
					/>
				);
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
	const icon =
		mention.kind === "directory" ? (
			<Folder size={14} />
		) : isArtifactMention(mention) ? (
			<Database size={14} />
		) : (
			<FileText size={14} />
		);
	return (
		<li className="attachment-preview source-mention">
			{icon}
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
	sqlArtifacts,
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
	const commandOptionRefs = useRef<Array<HTMLButtonElement | null>>([]);
	const fileMentionOptionRefs = useRef<Array<HTMLButtonElement | null>>([]);
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
		? activeFileMentionTrigger(input, sourceMentions)
		: null;
	const fileMentionMenuOpen = Boolean(fileMentionTrigger) && !isBusy;
	const sourceMentionItems = useMemo(
		() => indexedPathMentions(bootstrapSourceFiles, skills, sqlArtifacts),
		[bootstrapSourceFiles, skills, sqlArtifacts],
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
	const filteredSourceMentionSections = useMemo(
		() => sourceMentionSections(filteredSourceMentions),
		[filteredSourceMentions],
	);
	const commandIndex = useMemo(
		() =>
			buildCommandIndex({
				skills,
			}),
		[skills],
	);
	const filteredCommands = useMemo(() => {
		if (!commandMenuOpen) {
			return [];
		}
		return filterCommandIndex(commandIndex, commandQuery);
	}, [commandIndex, commandMenuOpen, commandQuery]);
	const filteredCommandSections = useMemo(
		() => commandSections(filteredCommands),
		[filteredCommands],
	);
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
	const activeCommandId =
		commandMenuOpen && filteredCommands[selectedCommandIndex]
			? `command-option-${filteredCommands[selectedCommandIndex].id}`
			: undefined;
	const activeFileMentionId =
		fileMentionMenuOpen && filteredSourceMentions[selectedFileMentionIndex]
			? `file-option-${filteredSourceMentions[selectedFileMentionIndex].id}`
			: undefined;
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
		sendMessage(
			{ text: prompt, files: currentFileParts },
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

	useEffect(() => {
		if (!commandMenuOpen) {
			return;
		}
		commandOptionRefs.current[selectedCommandIndex]?.scrollIntoView({
			block: "nearest",
		});
	}, [commandMenuOpen, selectedCommandIndex]);

	useEffect(() => {
		if (!fileMentionMenuOpen) {
			return;
		}
		fileMentionOptionRefs.current[selectedFileMentionIndex]?.scrollIntoView({
			block: "nearest",
		});
	}, [fileMentionMenuOpen, selectedFileMentionIndex]);

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
					const hasFileContext =
						currentFileParts.length > 0 || currentSourceFiles.length > 0;
					if (!text && !hasFileContext) {
						return;
					}
					sendMessage(
						{ text, files: currentFileParts },
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
						<div
							className="command-options"
							id="command-options"
							role="listbox"
						>
							{filteredCommands.length > 0 ? (
								filteredCommandSections.map((section) => (
									<Fragment key={section.category}>
										<span className="command-section-label">
											{COMMAND_CATEGORY_LABELS[section.category]}
										</span>
										{section.items.map(({ command, index: commandIndex }) => {
											const Icon = command.icon;
											return (
												<button
													type="button"
													className={
														commandIndex === selectedCommandIndex
															? "active"
															: ""
													}
													id={`command-option-${command.id}`}
													key={command.id}
													onMouseDown={(event) => event.preventDefault()}
													onMouseEnter={() =>
														setActiveCommandIndex(commandIndex)
													}
													onClick={() => submitCommandItem(command)}
													ref={(node) => {
														commandOptionRefs.current[commandIndex] = node;
													}}
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
										})}
									</Fragment>
								))
							) : (
								<p>No matching commands</p>
							)}
						</div>
					</section>
				)}
				{fileMentionMenuOpen && (
					<section className="command-palette" aria-label="File index">
						<div className="command-options" id="file-options" role="listbox">
							{filteredSourceMentions.length > 0 ? (
								filteredSourceMentionSections.map((section) => (
									<Fragment key={section.label}>
										<span className="command-section-label">
											{section.label}
										</span>
										{section.items.map(({ mention, index: mentionIndex }) => (
											<button
												type="button"
												className={
													mentionIndex === selectedFileMentionIndex
														? "active file-option"
														: "file-option"
												}
												id={`file-option-${mention.id}`}
												key={mention.id}
												onMouseDown={(event) => event.preventDefault()}
												onMouseEnter={() =>
													setActiveFileMentionIndex(mentionIndex)
												}
												onClick={() => selectSourceMention(mention)}
												ref={(node) => {
													fileMentionOptionRefs.current[mentionIndex] = node;
												}}
												role="option"
												aria-selected={
													mentionIndex === selectedFileMentionIndex
												}
											>
												{mention.kind === "directory" ? (
													<Folder size={15} />
												) : isArtifactMention(mention) ? (
													<Database size={15} />
												) : (
													<FileText size={15} />
												)}
												<span className="file-option-copy">
													<span>{mention.name}</span>
													{mention.detail && <small>{mention.detail}</small>}
												</span>
											</button>
										))}
									</Fragment>
								))
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
						aria-activedescendant={
							commandMenuOpen ? activeCommandId : activeFileMentionId
						}
						aria-controls={
							commandMenuOpen
								? "command-options"
								: fileMentionMenuOpen
									? "file-options"
									: undefined
						}
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
