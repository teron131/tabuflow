import type { ReactNode } from "react";

type MarkdownBlock =
	| { kind: "code"; key: string; language: string; text: string }
	| { kind: "heading"; key: string; level: number; text: string }
	| { kind: "list"; key: string; ordered: boolean; items: MarkdownListItem[] }
	| { kind: "paragraph"; key: string; text: string }
	| { kind: "quote"; key: string; text: string };

type MarkdownListItem = {
	key: string;
	depth: number;
	text: string;
};

type PendingMarkdownBlocks = {
	codeLanguage: string;
	codeLines: string[] | null;
	listItems: MarkdownListItem[];
	listOrdered: boolean;
	paragraphLines: string[];
	quoteLines: string[];
};

const CODE_FENCE_PATTERN = /^```([a-zA-Z0-9_-]*)\s*$/;
const HEADING_PATTERN = /^(#{1,6})\s+(.+)$/;
const QUOTE_PATTERN = /^>\s?(.+)$/;
const LIST_ITEM_PATTERN = /^(\s*)([-*+]|\d+[.)])\s+(.+)$/;
const INLINE_TOKEN_PATTERN =
	/(\[[^\]]+\]\([^)]+\)|`[^`\n]+`|\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*)/g;
const MAX_HEADING_LEVEL = 4;
const MIN_HEADING_LEVEL = 2;
const LIST_DEPTH_SPACES = 2;
const MAX_LIST_DEPTH = 3;
const SAFE_LINK_PREFIXES = ["https://", "http://", "mailto:", "/", "#"];

/** Renders the markdown subset used by streamed Workbench chat messages. */
export function ChatMarkdown({ content }: { content: string }) {
	return (
		<div className="message-markdown">
			{markdownBlocks(content).map((block) => (
				<MarkdownBlockView block={block} key={block.key} />
			))}
		</div>
	);
}

function MarkdownBlockView({ block }: { block: MarkdownBlock }) {
	if (block.kind === "heading") {
		const HeadingTag =
			`h${Math.min(Math.max(block.level, MIN_HEADING_LEVEL), MAX_HEADING_LEVEL)}` as
				| "h2"
				| "h3"
				| "h4";
		return (
			<HeadingTag className="message-markdown-heading">
				{renderInlineMarkdown(block.text)}
			</HeadingTag>
		);
	}
	if (block.kind === "list") {
		const ListTag = block.ordered ? "ol" : "ul";
		return (
			<ListTag className="message-markdown-list">
				{block.items.map((item) => (
					<li data-depth={item.depth} key={item.key}>
						{renderInlineMarkdown(item.text)}
					</li>
				))}
			</ListTag>
		);
	}
	if (block.kind === "code") {
		return (
			<pre className="message-markdown-code">
				<code data-language={block.language || undefined}>{block.text}</code>
			</pre>
		);
	}
	if (block.kind === "quote") {
		return (
			<blockquote className="message-markdown-quote">
				{renderInlineMarkdown(block.text)}
			</blockquote>
		);
	}
	return (
		<p className="message-markdown-paragraph">
			{renderInlineMarkdown(block.text)}
		</p>
	);
}

function markdownBlocks(markdown: string): MarkdownBlock[] {
	const blocks: MarkdownBlock[] = [];
	const pending: PendingMarkdownBlocks = {
		codeLanguage: "",
		codeLines: null,
		listItems: [],
		listOrdered: false,
		paragraphLines: [],
		quoteLines: [],
	};

	for (const rawLine of markdown.split(/\r\n|\r|\n/)) {
		const trimmedLine = rawLine.trim();
		const fenceMatch = CODE_FENCE_PATTERN.exec(trimmedLine);
		if (fenceMatch) {
			if (pending.codeLines) {
				flushCode(blocks, pending);
			} else {
				flushTextBlocks(blocks, pending);
				pending.codeLines = [];
				pending.codeLanguage = fenceMatch[1] || "";
			}
			continue;
		}

		if (pending.codeLines) {
			pending.codeLines.push(rawLine);
			continue;
		}

		if (!trimmedLine) {
			flushTextBlocks(blocks, pending);
			continue;
		}

		const headingMatch = HEADING_PATTERN.exec(trimmedLine);
		if (headingMatch) {
			flushTextBlocks(blocks, pending);
			blocks.push({
				kind: "heading",
				key: blockKey(blocks, "heading"),
				level: headingMatch[1].length,
				text: headingMatch[2].trim(),
			});
			continue;
		}

		const quoteMatch = QUOTE_PATTERN.exec(trimmedLine);
		if (quoteMatch) {
			flushParagraph(blocks, pending);
			flushList(blocks, pending);
			pending.quoteLines.push(quoteMatch[1].trim());
			continue;
		}

		const listMatch = LIST_ITEM_PATTERN.exec(rawLine);
		if (listMatch) {
			flushParagraph(blocks, pending);
			flushQuote(blocks, pending);
			const ordered = /^\d/.test(listMatch[2]);
			if (pending.listItems.length && ordered !== pending.listOrdered) {
				flushList(blocks, pending);
			}
			pending.listOrdered = ordered;
			pending.listItems.push({
				key: `item-${blocks.length}-${pending.listItems.length}`,
				depth: listDepth(listMatch[1]),
				text: listMatch[3].trim(),
			});
			continue;
		}

		flushList(blocks, pending);
		flushQuote(blocks, pending);
		pending.paragraphLines.push(trimmedLine);
	}

	flushTextBlocks(blocks, pending);
	flushCode(blocks, pending);
	return blocks;
}

function blockKey(blocks: MarkdownBlock[], kind: MarkdownBlock["kind"]) {
	return `${kind}-${blocks.length}`;
}

function flushTextBlocks(
	blocks: MarkdownBlock[],
	pending: PendingMarkdownBlocks,
) {
	flushParagraph(blocks, pending);
	flushList(blocks, pending);
	flushQuote(blocks, pending);
}

function flushParagraph(
	blocks: MarkdownBlock[],
	pending: PendingMarkdownBlocks,
) {
	if (!pending.paragraphLines.length) {
		return;
	}
	blocks.push({
		kind: "paragraph",
		key: blockKey(blocks, "paragraph"),
		text: pending.paragraphLines.join(" "),
	});
	pending.paragraphLines = [];
}

function flushList(blocks: MarkdownBlock[], pending: PendingMarkdownBlocks) {
	if (!pending.listItems.length) {
		return;
	}
	blocks.push({
		kind: "list",
		key: blockKey(blocks, "list"),
		ordered: pending.listOrdered,
		items: pending.listItems,
	});
	pending.listItems = [];
}

function flushQuote(blocks: MarkdownBlock[], pending: PendingMarkdownBlocks) {
	if (!pending.quoteLines.length) {
		return;
	}
	blocks.push({
		kind: "quote",
		key: blockKey(blocks, "quote"),
		text: pending.quoteLines.join(" "),
	});
	pending.quoteLines = [];
}

function flushCode(blocks: MarkdownBlock[], pending: PendingMarkdownBlocks) {
	if (!pending.codeLines) {
		return;
	}
	blocks.push({
		kind: "code",
		key: blockKey(blocks, "code"),
		language: pending.codeLanguage,
		text: pending.codeLines.join("\n"),
	});
	pending.codeLines = null;
	pending.codeLanguage = "";
}

function listDepth(indent: string) {
	return Math.min(
		Math.floor(indent.replaceAll("\t", "  ").length / LIST_DEPTH_SPACES),
		MAX_LIST_DEPTH,
	);
}

function renderInlineMarkdown(text: string) {
	const nodes: ReactNode[] = [];
	let cursor = 0;
	for (const token of text.matchAll(INLINE_TOKEN_PATTERN)) {
		const start = token.index;
		const value = token[0];
		if (start > cursor) {
			nodes.push(text.slice(cursor, start));
		}
		nodes.push(renderInlineToken(value, `${start}-${value}`));
		cursor = start + value.length;
	}
	if (cursor < text.length) {
		nodes.push(text.slice(cursor));
	}
	return nodes.length ? nodes : text;
}

function renderInlineToken(value: string, key: string) {
	if (value.startsWith("[")) {
		const linkMatch = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(value);
		const href = safeLinkHref(linkMatch?.[2] || "");
		if (linkMatch && href) {
			return (
				<a href={href} key={key} rel="noreferrer" target="_blank">
					{linkMatch[1]}
				</a>
			);
		}
		return value;
	}
	if (value.startsWith("`")) {
		return <code key={key}>{value.slice(1, -1)}</code>;
	}
	if (value.startsWith("**") || value.startsWith("__")) {
		return <strong key={key}>{value.slice(2, -2)}</strong>;
	}
	return <em key={key}>{value.slice(1, -1)}</em>;
}

function safeLinkHref(href: string) {
	const trimmedHref = href.trim();
	if (SAFE_LINK_PREFIXES.some((prefix) => trimmedHref.startsWith(prefix))) {
		return trimmedHref;
	}
	return "";
}
