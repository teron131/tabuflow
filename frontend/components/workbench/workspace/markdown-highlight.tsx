import type { ReactNode } from "react";

export function renderHighlightedMarkdownLine(line: string) {
	let lineClass = "";
	if (/^\s*```/.test(line) || /^---\s*$/.test(line)) {
		lineClass = "md-fence";
	} else if (/^\s{0,3}#{1,6}\s/.test(line)) {
		lineClass = "md-heading";
	} else if (/^\s{0,3}>/.test(line)) {
		lineClass = "md-quote";
	} else if (/^\s*[-*+]\s/.test(line) || /^\s*\d+\.\s/.test(line)) {
		lineClass = "md-list";
	} else if (/^\s*[A-Za-z0-9_-]+:\s/.test(line)) {
		lineClass = "md-key";
	}
	const content = renderMarkdownInline(line);
	return lineClass ? <span className={lineClass}>{content}</span> : content;
}

function renderMarkdownInline(line: string) {
	const parts = line.matchAll(
		/(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_|\[[^\]]+\]\([^)]+\))/g,
	);
	const nodes: ReactNode[] = [];
	let cursor = 0;
	for (const part of parts) {
		const start = part.index;
		const value = part[0];
		if (start > cursor) {
			nodes.push(line.slice(cursor, start));
		}
		const className = value.startsWith("`")
			? "md-code"
			: value.startsWith("[")
				? "md-link"
				: "md-emphasis";
		nodes.push(
			<span className={className} key={`md-${start}`}>
				{value}
			</span>,
		);
		cursor = start + value.length;
	}
	if (cursor < line.length) {
		nodes.push(line.slice(cursor));
	}
	return nodes.length ? nodes : line;
}
