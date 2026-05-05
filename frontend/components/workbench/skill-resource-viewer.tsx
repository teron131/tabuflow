import hljs from "highlight.js/lib/core";
import markdownLanguage from "highlight.js/lib/languages/markdown";
import pythonLanguage from "highlight.js/lib/languages/python";
import sqlLanguage from "highlight.js/lib/languages/sql";
import {
	FileCode2,
	type LucideIcon,
	RefreshCcw,
	ScrollText,
	Table2,
} from "lucide-react";
import {
	type ReactNode,
	useCallback,
	useEffect,
	useMemo,
	useRef,
	useState,
} from "react";
import {
	explainWorkspaceFile,
	type FileExplanation,
	type SkillResourceEntry,
	type SqlResult,
} from "@/lib/api";
import { CodeEditor } from "./code-editor";
import { renderHighlightedMarkdownLine } from "./markdown-highlight";
import { MarkdownContent } from "./markdown-viewer";
import { ResultTable } from "./result-table";
import type { InspectorView, RoundingSettings } from "./types";

type ResourceTab = "summary" | "code";

type SkillResourceViewerProps = {
	model: string;
	refreshNonce?: number;
	resource: SkillResourceEntry | null;
	resourceText?: string;
	rounding: RoundingSettings;
	showSummaryHeader?: boolean;
	showTabs?: boolean;
	viewMode?: ResourceTab;
	onResourceTextChange?: (text: string) => void;
};

type ResourceSummaryProps = {
	explanation: FileExplanation | null;
	onRegenerate: () => void;
	resource: SkillResourceEntry;
	showHeader: boolean;
	status: string;
};

const EXPLAINABLE_EXTENSIONS = new Set(["md", "markdown", "py", "sql"]);
const RESOURCE_LANGUAGE_BY_EXTENSION: Record<string, string> = {
	markdown: "markdown",
	md: "markdown",
	py: "python",
	sql: "sql",
};
const RESOURCE_ICON_BY_EXTENSION: Record<string, LucideIcon> = {
	csv: Table2,
	py: FileCode2,
	sql: Table2,
};

hljs.registerLanguage("markdown", markdownLanguage);
hljs.registerLanguage("python", pythonLanguage);
hljs.registerLanguage("sql", sqlLanguage);

export function SkillResourceViewer({
	model,
	refreshNonce = 0,
	resource,
	resourceText,
	rounding,
	showSummaryHeader = true,
	showTabs = true,
	viewMode,
	onResourceTextChange,
}: SkillResourceViewerProps) {
	const [activeTab, setActiveTab] = useState<ResourceTab>("summary");
	const [explanation, setExplanation] = useState<FileExplanation | null>(null);
	const [summaryStatus, setSummaryStatus] = useState("");
	const previousRefreshNonce = useRef(refreshNonce);
	const csvResult = useMemo(
		() => (resource && isCsvResource(resource) ? csvToResult(resource) : null),
		[resource],
	);
	const canExplain = isExplainableResource(resource);
	const selectedTab = viewMode || activeTab;

	const loadExplanation = useCallback(
		async ({ force = false }: { force?: boolean } = {}) => {
			const path = resourcePath(resource);
			if (!path || !canExplain) {
				return;
			}
			setSummaryStatus(force ? "Regenerating summary" : "Loading summary");
			try {
				const nextExplanation = await explainWorkspaceFile({
					path,
					force,
					model,
				});
				setExplanation(nextExplanation);
				setSummaryStatus("");
			} catch (error) {
				setSummaryStatus((error as Error).message);
				setExplanation(null);
			}
		},
		[canExplain, model, resource],
	);

	useEffect(() => {
		if (!viewMode) {
			setActiveTab(canExplain ? "summary" : "code");
		}
		setExplanation(null);
		setSummaryStatus("");
		if (canExplain && selectedTab === "summary") {
			void loadExplanation();
		}
	}, [canExplain, loadExplanation, selectedTab, viewMode]);

	useEffect(() => {
		const didChange = refreshNonce !== previousRefreshNonce.current;
		previousRefreshNonce.current = refreshNonce;
		if (!didChange || refreshNonce === 0) {
			return;
		}
		if (canExplain && selectedTab === "summary") {
			void loadExplanation({ force: true });
		}
	}, [canExplain, loadExplanation, refreshNonce, selectedTab]);

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
			{canExplain && showTabs ? (
				<ResourceTabs
					activeTab={selectedTab}
					contentLabel={resourceContentTabLabel(resource)}
					onRegenerate={() => loadExplanation({ force: true })}
					onSelectTab={setActiveTab}
				/>
			) : null}
			{selectedTab === "summary" && canExplain ? (
				<ResourceSummary
					explanation={explanation}
					onRegenerate={() => loadExplanation({ force: true })}
					resource={resource}
					showHeader={showSummaryHeader}
					status={summaryStatus}
				/>
			) : (
				<ResourceCodeViewer
					resource={resource}
					resourceText={resourceText}
					onResourceTextChange={onResourceTextChange}
				/>
			)}
		</div>
	);
}

function ResourceTabs({
	activeTab,
	contentLabel,
	onRegenerate,
	onSelectTab,
}: {
	activeTab: ResourceTab;
	contentLabel: string;
	onRegenerate: () => void;
	onSelectTab: (tab: ResourceTab) => void;
}) {
	return (
		<fieldset className="resource-tabs">
			<legend className="sr-only">File preview mode</legend>
			<button
				className={activeTab === "summary" ? "active" : ""}
				onClick={() => onSelectTab("summary")}
				type="button"
			>
				Summary
			</button>
			<button
				className={activeTab === "code" ? "active" : ""}
				onClick={() => onSelectTab("code")}
				type="button"
			>
				{contentLabel}
			</button>
			<button
				aria-label="Regenerate summary"
				className="icon-button outline-button"
				onClick={onRegenerate}
				title="Regenerate summary"
				type="button"
			>
				<RefreshCcw size={13} />
			</button>
		</fieldset>
	);
}

export function skillResourceIcon(
	resource: SkillResourceEntry | null,
): LucideIcon {
	const extension = resource ? resourceExtension(resource).toLowerCase() : "";
	return RESOURCE_ICON_BY_EXTENSION[extension] || ScrollText;
}

export function isCsvSkillResource(
	resource: SkillResourceEntry | null,
	inspectorView: InspectorView,
) {
	return inspectorView === "skillResource" && resource
		? isCsvResource(resource)
		: false;
}

export function isMarkdownSkillResource(resource: SkillResourceEntry | null) {
	return resource ? isMarkdownResource(resource) : false;
}

function ResourceCodeViewer({
	resource,
	resourceText,
	onResourceTextChange,
}: {
	resource: SkillResourceEntry;
	resourceText?: string;
	onResourceTextChange?: (text: string) => void;
}) {
	const [localContent, setLocalContent] = useState(resource.content || "");
	const language = resourceLanguage(resource);
	const editorText = resourceText ?? localContent;
	const shouldWrapLines = language !== "python";
	const renderLine = useCallback(
		(line: string) => renderHighlightedResourceText(line, language),
		[language],
	);

	useEffect(() => {
		setLocalContent(resource.content || "");
	}, [resource]);

	return (
		<CodeEditor
			ariaLabel={`${resource.label} editor`}
			className={
				shouldWrapLines
					? "resource-editor-wrap"
					: "resource-editor-wrap editor-no-wrap"
			}
			highlightClassName={`resource-highlight language-${language}`}
			renderLine={renderLine}
			value={editorText}
			wrap={shouldWrapLines ? "soft" : "off"}
			onChange={onResourceTextChange || setLocalContent}
		/>
	);
}

function ResourceSummary({
	explanation,
	onRegenerate,
	resource,
	showHeader,
	status,
}: ResourceSummaryProps) {
	if (status) {
		return <ResourceSummaryStatus status={status} />;
	}
	if (!explanation) {
		return <ResourceSummaryStatus status="Preparing summary" />;
	}
	return (
		<article className="resource-summary-panel">
			{showHeader ? (
				<header>
					<strong>{resource.relative_path || resource.label}</strong>
					<button
						aria-label="Regenerate summary"
						className="icon-button outline-button"
						onClick={onRegenerate}
						title="Regenerate summary"
						type="button"
					>
						<RefreshCcw size={13} />
					</button>
				</header>
			) : null}
			<MarkdownContent
				content={explanation.summary}
				keyPrefix={explanation.content_hash}
			/>
		</article>
	);
}

function ResourceSummaryStatus({ status }: { status: string }) {
	const isPending = /summary$/i.test(status);
	return (
		<article className="resource-summary-panel">
			<div
				className={
					isPending
						? "resource-summary-status"
						: "resource-summary-status error"
				}
			>
				<span>{status}</span>
				{isPending ? (
					<div className="resource-summary-status-lines" aria-hidden="true">
						<i />
						<i />
						<i />
					</div>
				) : null}
			</div>
		</article>
	);
}

function renderHighlightedResourceText(line: string, language: string) {
	if (language === "markdown") {
		return renderHighlightedMarkdownLine(line);
	}
	if (language === "plaintext") {
		return line;
	}
	const highlighted = hljs.highlight(line, {
		language,
		ignoreIllegals: true,
	}).value;
	return highlighted ? renderHighlightNodes(highlighted, line) : line;
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
	return RESOURCE_LANGUAGE_BY_EXTENSION[extension] || "plaintext";
}

function isCsvResource(resource: SkillResourceEntry) {
	return resourceExtension(resource).toLowerCase() === "csv";
}

function isMarkdownResource(resource: SkillResourceEntry) {
	const extension = resourceExtension(resource).toLowerCase();
	return extension === "md" || extension === "markdown";
}

export function isExplainableSkillResource(
	resource: SkillResourceEntry | null,
	inspectorView: InspectorView,
) {
	return inspectorView === "skillResource" && isExplainableResource(resource);
}

function isExplainableResource(resource: SkillResourceEntry | null) {
	if (!resource) return false;
	const extension = resourceExtension(resource).toLowerCase();
	return EXPLAINABLE_EXTENSIONS.has(extension);
}

function resourceExtension(resource: SkillResourceEntry) {
	const name = resource.relative_path || resource.label;
	const extension = name.split(".").at(-1);
	return extension && extension !== name ? extension : "";
}

function resourcePath(resource: SkillResourceEntry | null) {
	return resource?.path || resource?.relative_path || "";
}

function resourceContentTabLabel(resource: SkillResourceEntry) {
	return isMarkdownResource(resource) ? "Markdown" : "Code";
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
