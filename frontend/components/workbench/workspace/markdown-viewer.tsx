import type { ReactNode } from "react";

type MarkdownBlock =
	| { kind: "heading"; key: string; text: string }
	| { kind: "paragraph"; key: string; text: string }
	| { kind: "list"; key: string; items: Array<{ key: string; text: string }> };

type MarkdownContentProps = {
	content: string;
	keyPrefix: string;
};

type MarkdownViewerProps = MarkdownContentProps;

export function MarkdownViewer({ content, keyPrefix }: MarkdownViewerProps) {
	return (
		<article className="resource-markdown-viewer">
			<MarkdownContent content={content} keyPrefix={keyPrefix} />
		</article>
	);
}

export function MarkdownContent({ content, keyPrefix }: MarkdownContentProps) {
	return (
		<div className="resource-summary-text">
			{markdownBlocks(content).map((block) => (
				<MarkdownBlockView block={block} key={`${keyPrefix}-${block.key}`} />
			))}
		</div>
	);
}

function MarkdownBlockView({ block }: { block: MarkdownBlock }) {
	if (block.kind === "heading") {
		return (
			<h4 className="resource-summary-heading">
				{renderInlineMarkdown(block.text)}
			</h4>
		);
	}
	if (block.kind === "list") {
		return (
			<ul className="resource-summary-list">
				{block.items.map((item) => (
					<li key={item.key}>{renderInlineMarkdown(item.text)}</li>
				))}
			</ul>
		);
	}
	return (
		<p className="resource-summary-paragraph">
			{renderInlineMarkdown(block.text)}
		</p>
	);
}

function markdownBlocks(markdown: string): MarkdownBlock[] {
	const seenCounts = new Map<string, number>();
	const blocks: MarkdownBlock[] = [];
	let paragraphLines: string[] = [];
	let listItems: Array<{ key: string; text: string }> = [];

	function uniqueKey(text: string) {
		const keyBase = text
			.toLowerCase()
			.replace(/[^a-z0-9]+/g, "-")
			.slice(0, 60);
		const seenCount = seenCounts.get(keyBase) || 0;
		seenCounts.set(keyBase, seenCount + 1);
		return `${keyBase || "markdown"}-${seenCount}`;
	}

	function flushParagraph() {
		if (!paragraphLines.length) {
			return;
		}
		const text = paragraphLines.join(" ");
		blocks.push({ kind: "paragraph", key: uniqueKey(text), text });
		paragraphLines = [];
	}

	function flushList() {
		if (!listItems.length) {
			return;
		}
		const key = uniqueKey(listItems.map((item) => item.text).join(" "));
		blocks.push({ kind: "list", key, items: listItems });
		listItems = [];
	}

	for (const rawLine of markdown.split(/\r\n|\r|\n/)) {
		const line = rawLine.trim();
		if (!line) {
			flushParagraph();
			flushList();
			continue;
		}

		const headingMatch = /^(#{1,6})\s+(.+)$/.exec(line);
		if (headingMatch) {
			flushParagraph();
			flushList();
			const text = headingMatch[2].trim();
			blocks.push({ kind: "heading", key: uniqueKey(text), text });
			continue;
		}

		const listMatch = /^(?:[-*+]|\d+[.)])\s+(.+)$/.exec(line);
		if (listMatch) {
			flushParagraph();
			const text = listMatch[1].trim();
			listItems.push({ key: uniqueKey(text), text });
			continue;
		}

		flushList();
		paragraphLines.push(line);
	}

	flushParagraph();
	flushList();
	return blocks;
}

function renderInlineMarkdown(text: string) {
	const tokenPattern =
		/(\[[^\]]+\]\([^)]+\)|`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_)/g;
	const nodes: ReactNode[] = [];
	let cursor = 0;
	for (const token of text.matchAll(tokenPattern)) {
		const start = token.index;
		const value = token[0];
		if (start > cursor) {
			nodes.push(text.slice(cursor, start));
		}
		nodes.push(renderInlineToken(value, `${value}-${start}`));
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
		if (linkMatch) {
			return (
				<a href={linkMatch[2]} key={key}>
					{linkMatch[1]}
				</a>
			);
		}
	}
	if (value.startsWith("`")) {
		return <code key={key}>{value.slice(1, -1)}</code>;
	}
	if (value.startsWith("**") || value.startsWith("__")) {
		return <strong key={key}>{value.slice(2, -2)}</strong>;
	}
	return <em key={key}>{value.slice(1, -1)}</em>;
}
