import hljs from "highlight.js/lib/core";
import markdownLanguage from "highlight.js/lib/languages/markdown";
import pythonLanguage from "highlight.js/lib/languages/python";
import sqlLanguage from "highlight.js/lib/languages/sql";
import {
	FileCode2,
	type LucideIcon,
	ScrollText,
	Table2,
} from "lucide-react";
import { type ReactNode, useMemo } from "react";
import type { SkillResourceEntry, SqlResult } from "@/lib/api";
import { ResultTable } from "./result-table";
import type { InspectorView, RoundingSettings } from "./types";

type HighlightedResourceLine = {
	key: string;
	number: number;
	nodes: ReactNode[];
};

hljs.registerLanguage("markdown", markdownLanguage);
hljs.registerLanguage("python", pythonLanguage);
hljs.registerLanguage("sql", sqlLanguage);

export function SkillResourceViewer({
	resource,
	rounding,
}: {
	resource: SkillResourceEntry | null;
	rounding: RoundingSettings;
}) {
	const csvResult = useMemo(
		() => (resource && isCsvResource(resource) ? csvToResult(resource) : null),
		[resource],
	);

	if (!resource) {
		return <div className="empty-state">Select a skill file in Explorer.</div>;
	}
	if (!resource.content) {
		return (
			<div className="empty-state">
				{resource.relative_path || resource.label} has no preview content.
			</div>
		);
	}
	if (csvResult) {
		return (
			<div className="target-viewer">
				<div className="target-preview-grid">
					<ResultTable result={csvResult} rounding={rounding} />
				</div>
			</div>
		);
	}
	return (
		<div className="resource-viewer">
			<ResourceCodeViewer resource={resource} />
		</div>
	);
}

export function skillResourceIcon(
	resource: SkillResourceEntry | null,
): LucideIcon {
	const extension = resource ? resourceExtension(resource).toLowerCase() : "";
	if (extension === "csv") return Table2;
	if (extension === "py") return FileCode2;
	if (extension === "sql") return Table2;
	return ScrollText;
}

export function isCsvSkillResource(
	resource: SkillResourceEntry | null,
	inspectorView: InspectorView,
) {
	return inspectorView === "skillResource" && resource
		? isCsvResource(resource)
		: false;
}

function ResourceCodeViewer({ resource }: { resource: SkillResourceEntry }) {
	const lines = useMemo(() => highlightedResourceLines(resource), [resource]);
	return (
		<pre className="resource-code-viewer">
			<code className={`hljs language-${resourceLanguage(resource)}`}>
				{lines.map((line) => (
					<span className="resource-code-line" key={line.key}>
						<span className="resource-code-line-number">{line.number}</span>
						<span className="resource-code-line-text">
							{line.nodes.length ? line.nodes : "\u00a0"}
						</span>
					</span>
				))}
			</code>
		</pre>
	);
}

function highlightedResourceLines(
	resource: SkillResourceEntry,
): HighlightedResourceLine[] {
	const language = resourceLanguage(resource);
	return rawResourceLines(resource.content || "").map((line) => {
		if (language === "plaintext") {
			return {
				...line,
				nodes: line.text ? [line.text] : [],
			};
		}
		const highlighted = hljs.highlight(line.text, {
			language,
			ignoreIllegals: true,
		}).value;
		return {
			...line,
			nodes: highlighted ? renderHighlightNodes(highlighted, line.key) : [],
		};
	});
}

function rawResourceLines(text: string) {
	const matches = text.matchAll(/[^\r\n]*(?:\r\n|\r|\n|$)/g);
	const lines: Array<{ key: string; number: number; text: string }> = [];
	for (const match of matches) {
		if (match.index === text.length && match[0] === "") {
			continue;
		}
		lines.push({
			key: `resource-line-${match.index}`,
			number: lines.length + 1,
			text: match[0].replace(/\r\n|\r|\n$/, ""),
		});
	}
	return lines.length
		? lines
		: [{ key: "resource-line-0", number: 1, text: "" }];
}

function renderHighlightNodes(html: string, keyPrefix: string) {
	const nodes: ReactNode[] = [];
	const tokenPattern = /<span class="([^"]+)">([\s\S]*?)<\/span>/g;
	let cursor = 0;
	for (const token of html.matchAll(tokenPattern)) {
		const start = token.index;
		if (start > cursor) {
			nodes.push(decodeHighlightHtml(html.slice(cursor, start)));
		}
		nodes.push(
			<span className={token[1]} key={`${keyPrefix}-token-${start}`}>
				{decodeHighlightHtml(stripHighlightTags(token[2]))}
			</span>,
		);
		cursor = start + token[0].length;
	}
	if (cursor < html.length) {
		nodes.push(decodeHighlightHtml(html.slice(cursor)));
	}
	return nodes;
}

function stripHighlightTags(html: string) {
	return html.replaceAll(/<\/?span(?:\s+class="[^"]+")?>/g, "");
}

function decodeHighlightHtml(html: string) {
	return html
		.replaceAll("&lt;", "<")
		.replaceAll("&gt;", ">")
		.replaceAll("&quot;", '"')
		.replaceAll("&#039;", "'")
		.replaceAll("&amp;", "&");
}

function resourceLanguage(resource: SkillResourceEntry) {
	const extension = resourceExtension(resource).toLowerCase();
	if (extension === "py") return "python";
	if (extension === "sql") return "sql";
	if (extension === "md" || extension === "markdown") return "markdown";
	return "plaintext";
}

function isCsvResource(resource: SkillResourceEntry) {
	return resourceExtension(resource).toLowerCase() === "csv";
}

function resourceExtension(resource: SkillResourceEntry) {
	const name = resource.relative_path || resource.label;
	const extension = name.split(".").at(-1);
	return extension && extension !== name ? extension : "";
}

function csvToResult(resource: SkillResourceEntry): SqlResult {
	const rows = parseCsv(resource.content || "");
	if (!rows.length) {
		return {
			status: "ok",
			summary: "CSV file is empty.",
			columns: [],
			rows: [],
			row_count: 0,
		};
	}
	const [columns, ...records] = rows;
	const safeColumns = columns.map(
		(column, index) => column || `column_${index + 1}`,
	);
	return {
		status: "ok",
		columns: safeColumns,
		rows: records.map((record) =>
			Object.fromEntries(
				safeColumns.map((column, index) => [column, record[index] ?? ""]),
			),
		),
		row_count: Math.max(0, rows.length - 1),
	};
}

function parseCsv(text: string): string[][] {
	const rows: string[][] = [];
	let row: string[] = [];
	let cell = "";
	let inQuotes = false;

	for (let index = 0; index < text.length; index += 1) {
		const char = text[index];
		const nextChar = text[index + 1];
		if (char === '"') {
			if (inQuotes && nextChar === '"') {
				cell += '"';
				index += 1;
			} else {
				inQuotes = !inQuotes;
			}
			continue;
		}
		if (char === "," && !inQuotes) {
			row.push(cell);
			cell = "";
			continue;
		}
		if ((char === "\n" || char === "\r") && !inQuotes) {
			if (char === "\r" && nextChar === "\n") {
				index += 1;
			}
			row.push(cell);
			rows.push(row);
			row = [];
			cell = "";
			continue;
		}
		cell += char;
	}
	if (cell || row.length || text.endsWith(",")) {
		row.push(cell);
		rows.push(row);
	}
	return rows.filter((cells) =>
		cells.some((cellValue) => cellValue.length > 0),
	);
}
