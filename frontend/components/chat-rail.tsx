"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import {
	Bot,
	CheckCircle2,
	ChevronDown,
	CircleUserRound,
	Loader2,
	Send,
	TriangleAlert,
	Wrench,
} from "lucide-react";
import { useMemo, useState } from "react";
import type { WorkbenchMessage } from "@/lib/chat-contracts";

type WorkbenchPart = WorkbenchMessage["parts"][number];
type WorkbenchToolPart = Extract<WorkbenchPart, { toolCallId: string }>;

type ChatRailProps = {
	modelOptions: string[];
	selectedModel: string;
	onModelChange: (model: string) => void;
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

function textFromMessage(message: WorkbenchMessage) {
	return message.parts
		.filter((part) => part.type === "text")
		.map((part) => part.text)
		.join("\n");
}

function isToolPart(part: WorkbenchPart): part is WorkbenchToolPart {
	return part.type.startsWith("tool-") || part.type === "dynamic-tool";
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
	const isError = part.state === "output-error";

	return (
		<details className="tool-message">
			<summary>
				<span className="tool-message-icon">
					{isError ? <TriangleAlert size={13} /> : <Wrench size={13} />}
				</span>
				<span>{toolTitle(part)}</span>
				<span className={isError ? "tool-state error" : "tool-state"}>
					{part.state.replaceAll("-", " ")}
				</span>
				<ChevronDown size={13} className="tool-chevron" />
			</summary>
			<div className="tool-message-body">
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
				{errorText && (
					<div>
						<span className="field-label">error</span>
						<pre>{errorText}</pre>
					</div>
				)}
			</div>
		</details>
	);
}

function ChatMessage({ message }: { message: WorkbenchMessage }) {
	const isUser = message.role === "user";
	const text = textFromMessage(message);
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
				{text && <p>{text}</p>}
				{toolParts.map((part) => (
					<ToolMessage key={part.toolCallId} part={part} />
				))}
			</div>
		</article>
	);
}

export function ChatRail({
	modelOptions,
	selectedModel,
	onModelChange,
}: ChatRailProps) {
	const [input, setInput] = useState("");
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

			<div className="message-stream" role="log" aria-label="Chat messages">
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
				className="composer"
				onSubmit={(event) => {
					event.preventDefault();
					const text = input.trim();
					if (!text) {
						return;
					}
					sendMessage({ role: "user", parts: [{ type: "text", text }] });
					setInput("");
				}}
			>
				<textarea
					aria-label="Ask the data agent"
					onChange={(event) => setInput(event.target.value)}
					onKeyDown={(event) => {
						if (event.key === "Enter" && !event.shiftKey) {
							event.preventDefault();
							event.currentTarget.form?.requestSubmit();
						}
					}}
					placeholder="Ask about the active data workspace..."
					value={input}
				/>
				<button
					type={isBusy ? "button" : "submit"}
					onClick={isBusy ? stop : undefined}
					aria-label={isBusy ? "Stop generation" : "Send message"}
				>
					{isBusy ? <Loader2 className="spin" size={16} /> : <Send size={16} />}
				</button>
			</form>
		</aside>
	);
}
