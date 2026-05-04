import {
	Activity,
	ChevronDown,
	ChevronRight,
	Download,
	FileCode2,
	FileText,
	type LucideIcon,
	Play,
	RefreshCcw,
	RotateCcw,
	Save,
	ScrollText,
	Star,
	Table2,
} from "lucide-react";
import type {
	ReactNode,
	PointerEvent as ReactPointerEvent,
	RefObject,
} from "react";
import { useCallback, useState } from "react";
import {
	type BootstrapPayload,
	type SkillEntry,
	type SkillResourceEntry,
	type SourceFile,
	type SqlResult,
	skillLineCount,
	type Target,
} from "@/lib/api";
import { CodeEditor } from "./code-editor";
import { downloadResult } from "./download";
import { ResultTable } from "./result-table";
import {
	isCsvSkillResource,
	isExplainableSkillResource,
	isMarkdownSkillResource,
	SkillResourceViewer,
	skillResourceIcon,
} from "./skill-resource-viewer";
import { SourceViewer } from "./source-viewer";
import { renderHighlightedSql } from "./sql";
import { isTargetView } from "./targets";
import type { InspectorView, RoundingSettings } from "./types";

type CollapsiblePane = "top" | "output";

type WorkspacePanelProps = {
	bootstrap: BootstrapPayload;
	centerRef: RefObject<HTMLElement | null>;
	inspectorView: InspectorView;
	isPreviewingTarget: boolean;
	isPreviewingSource: boolean;
	isQueryVisible: boolean;
	isRunningSql: boolean;
	isSkillSaved: boolean;
	rounding: RoundingSettings;
	selectedSkill: SkillEntry | null;
	selectedSkillResource: SkillResourceEntry | null;
	selectedModel: string;
	selectedSource: SourceFile | null;
	selectedTarget: Target | null;
	sourcePreviewResult: SqlResult | null;
	skillText: string;
	sql: string;
	sqlResult: SqlResult | null;
	targetPreviewResult: SqlResult | null;
	targetSourceFileName: string | null;
	targetSourceName: string | null;
	onRevertSkill: () => void;
	onRunSql: () => void;
	onSaveSkill: () => void;
	onSkillTextChange: (text: string) => void;
	onSqlChange: (sql: string) => void;
	onVerticalResize: (event: ReactPointerEvent) => void;
};

export function WorkspacePanel({
	bootstrap,
	centerRef,
	inspectorView,
	isPreviewingTarget,
	isPreviewingSource,
	isQueryVisible,
	isRunningSql,
	isSkillSaved,
	rounding,
	selectedSkill,
	selectedSkillResource,
	selectedModel,
	selectedSource,
	selectedTarget,
	sourcePreviewResult,
	skillText,
	sql,
	sqlResult,
	targetPreviewResult,
	targetSourceFileName,
	targetSourceName,
	onRevertSkill,
	onRunSql,
	onSaveSkill,
	onSkillTextChange,
	onSqlChange,
	onVerticalResize,
}: WorkspacePanelProps) {
	const [topCollapsed, setTopCollapsed] = useState(false);
	const [outputCollapsed, setOutputCollapsed] = useState(false);
	const [summaryRefreshNonce, setSummaryRefreshNonce] = useState(0);
	const hasSummaryPane = isExplainableSkillResource(
		selectedSkillResource,
		inspectorView,
	);
	const hasTopPane = isQueryVisible || hasSummaryPane;
	const summaryResourcePath =
		selectedSkillResource?.relative_path || selectedSkillResource?.label || "";
	const renderSqlLine = useCallback(
		(line: string) => renderHighlightedSql(line),
		[],
	);
	const renderSkillLine = useCallback(
		(line: string) => renderHighlightedMarkdown(line),
		[],
	);
	const togglePane = useCallback((pane: CollapsiblePane) => {
		if (pane === "top") {
			setTopCollapsed((collapsed) => !collapsed);
			return;
		}
		setOutputCollapsed((collapsed) => !collapsed);
	}, []);
	const regenerateSummary = useCallback(() => {
		setSummaryRefreshNonce((nonce) => nonce + 1);
	}, []);
	const inspector = inspectorState({
		bootstrap,
		inspectorView,
		selectedSkill,
		selectedSkillResource,
		selectedSource,
		selectedTarget,
		sourcePreviewResult,
		sqlResult,
		targetPreviewResult,
		targetSourceFileName,
		targetSourceName,
	});
	const InspectorIcon = inspector.icon;

	return (
		<section ref={centerRef} className="workspace">
			<div
				className={[
					"workspace-grid",
					!hasTopPane ? "query-hidden" : "",
					topCollapsed ? "query-collapsed" : "",
					outputCollapsed ? "output-collapsed" : "",
				]
					.filter(Boolean)
					.join(" ")}
			>
				{hasTopPane ? (
					<section className="query-pane">
						<header className="pane-header collapsible-pane-header">
							<button
								aria-controls="query-pane-body"
								aria-expanded={!topCollapsed}
								className="pane-toggle"
								onClick={() => togglePane("top")}
								type="button"
							>
								{topCollapsed ? (
									<ChevronRight size={14} aria-hidden="true" />
								) : (
									<ChevronDown size={14} aria-hidden="true" />
								)}
								{isQueryVisible ? (
									"QUERY BUFFER"
								) : (
									<>
										<Star size={14} aria-hidden="true" />
										<span className="inspector-title">AI SUMMARY</span>
										<span className="inspector-detail">
											{summaryResourcePath}
										</span>
									</>
								)}
							</button>
							{isQueryVisible ? (
								<div className="button-cluster">
									<button
										className="primary-button"
										type="button"
										onClick={onRunSql}
										disabled={isRunningSql}
									>
										<Play size={13} />
										{isRunningSql ? "Running" : "Run"}
									</button>
								</div>
							) : (
								<button
									aria-label="Regenerate AI summary"
									className="outline-button icon-button"
									onClick={regenerateSummary}
									title="Regenerate AI summary"
									type="button"
								>
									<RefreshCcw size={13} />
								</button>
							)}
						</header>
						{!topCollapsed ? (
							<div id="query-pane-body" className="query-pane-body">
								{isQueryVisible ? (
									<CodeEditor
										ariaLabel="SQL editor"
										className="sql-editor-wrap"
										highlightClassName="sql-highlight"
										renderLine={renderSqlLine}
										value={sql}
										onChange={onSqlChange}
									/>
								) : (
									<SkillResourceViewer
										model={selectedModel}
										resource={selectedSkillResource}
										rounding={rounding}
										refreshNonce={summaryRefreshNonce}
										showSummaryHeader={false}
										showTabs={false}
										viewMode="summary"
									/>
								)}
							</div>
						) : null}
					</section>
				) : null}

				{hasTopPane && !topCollapsed && !outputCollapsed ? (
					<button
						className="resize-handle vertical-handle"
						onPointerDown={onVerticalResize}
						type="button"
						aria-label="Resize workspace panes"
					/>
				) : null}

				<section
					className={
						inspectorView === "results"
							? "output-pane result-output"
							: "output-pane"
					}
				>
					<header className="pane-header collapsible-pane-header">
						<button
							aria-controls="output-pane-body"
							aria-expanded={!outputCollapsed}
							className="pane-toggle"
							onClick={() => togglePane("output")}
							type="button"
						>
							{outputCollapsed ? (
								<ChevronRight size={14} aria-hidden="true" />
							) : (
								<ChevronDown size={14} aria-hidden="true" />
							)}
							<InspectorIcon size={14} aria-hidden="true" />
							<span className="inspector-title">{inspector.title}</span>
							<span className="inspector-detail">{inspector.detail}</span>
						</button>
						{inspectorView === "results" && sqlResult?.columns?.length ? (
							<button
								className="outline-button"
								type="button"
								onClick={() => downloadResult(sqlResult)}
							>
								<Download size={13} />
								CSV
							</button>
						) : null}
					</header>
					{!outputCollapsed ? (
						<div
							id="output-pane-body"
							className={
								inspectorView === "results" ||
								isCsvSkillResource(selectedSkillResource, inspectorView) ||
								hasSummaryPane
									? "pane-body result-pane-body"
									: "pane-body"
							}
						>
							{inspectorView === "run" && (
								<div className="summary-grid">
									{bootstrap.stage_cards.map((card) => (
										<section className="telemetry-tile" key={card.name}>
											<span>{card.status}</span>
											<strong>{card.name}</strong>
											<p>{card.summary}</p>
										</section>
									))}
								</div>
							)}
							{inspectorView === "results" && (
								<ResultTable result={sqlResult} rounding={rounding} />
							)}
							{inspectorView === "target" && (
								<div className="target-viewer">
									<div className="target-preview-grid">
										{isPreviewingTarget ? (
											<div className="empty-state">Loading preview</div>
										) : targetPreviewResult ? (
											<ResultTable
												result={targetPreviewResult}
												rounding={rounding}
											/>
										) : (
											<div className="empty-state">
												Select a table or view to preview rows.
											</div>
										)}
									</div>
								</div>
							)}
							{inspectorView === "skill" && (
								<div className="skill-editor">
									<header>
										<h3>
											{selectedSkill && !isSkillSaved ? (
												<span className="dirty-dot" aria-hidden="true" />
											) : null}
											<span>{selectedSkill?.name || "No skill selected"}</span>
											{selectedSkill && !isSkillSaved ? (
												<span className="sr-only">Unsaved changes</span>
											) : null}
										</h3>
										<div className="skill-meta">
											<span>
												{selectedSkill
													? `${skillLineCount(selectedSkill)} LINES`
													: "-"}
											</span>
											<span>{formatSkillModifiedAt(selectedSkill)}</span>
										</div>
										<button
											aria-label="Save skill"
											className="outline-button icon-button"
											disabled={!selectedSkill}
											onClick={onSaveSkill}
											title="Save skill"
											type="button"
										>
											<Save size={13} />
										</button>
										<button
											aria-label="Revert to saved skill"
											className="outline-button icon-button"
											disabled={!selectedSkill || isSkillSaved}
											onClick={onRevertSkill}
											title="Revert to saved skill"
											type="button"
										>
											<RotateCcw size={13} />
										</button>
									</header>
									<CodeEditor
										className="skill-editor-wrap"
										highlightClassName="skill-highlight"
										renderLine={renderSkillLine}
										value={skillText}
										onChange={onSkillTextChange}
									/>
								</div>
							)}
							{inspectorView === "skillResource" && (
								<SkillResourceViewer
									model={selectedModel}
									resource={selectedSkillResource}
									rounding={rounding}
									showTabs={!hasSummaryPane}
									viewMode={hasSummaryPane ? "code" : undefined}
								/>
							)}
							{inspectorView === "source" && (
								<SourceViewer
									isPreviewingSource={isPreviewingSource}
									rounding={rounding}
									selectedSource={selectedSource}
									sourcePreviewResult={sourcePreviewResult}
								/>
							)}
						</div>
					) : null}
				</section>
			</div>
		</section>
	);
}

function inspectorState({
	bootstrap,
	inspectorView,
	selectedSkill,
	selectedSkillResource,
	selectedSource,
	selectedTarget,
	sourcePreviewResult,
	sqlResult,
	targetPreviewResult,
	targetSourceFileName,
	targetSourceName,
}: {
	bootstrap: BootstrapPayload;
	inspectorView: InspectorView;
	selectedSkill: SkillEntry | null;
	selectedSkillResource: SkillResourceEntry | null;
	selectedSource: SourceFile | null;
	selectedTarget: Target | null;
	sourcePreviewResult: SqlResult | null;
	sqlResult: SqlResult | null;
	targetPreviewResult: SqlResult | null;
	targetSourceFileName: string | null;
	targetSourceName: string | null;
}): { title: ReactNode; detail: string; icon: LucideIcon } {
	if (inspectorView === "results") {
		return { title: "Results", detail: resultDetail(sqlResult), icon: Table2 };
	}
	if (inspectorView === "target") {
		return {
			title: targetTitle(
				selectedTarget,
				targetSourceName,
				targetSourceFileName,
			),
			detail: targetDetail(selectedTarget, targetPreviewResult),
			icon: selectedTarget && isTargetView(selectedTarget) ? FileCode2 : Table2,
		};
	}
	if (inspectorView === "skill") {
		return {
			title: selectedSkill?.name || "No skill selected",
			detail: selectedSkill
				? `${skillLineCount(selectedSkill)} lines`
				: "skill",
			icon: ScrollText,
		};
	}
	if (inspectorView === "skillResource") {
		return {
			title: skillResourceTitle(selectedSkillResource),
			detail: skillResourceDetail(selectedSkillResource),
			icon: skillResourceIcon(selectedSkillResource),
		};
	}
	if (inspectorView === "source") {
		return {
			title: selectedSource?.name || "No source selected",
			detail: sourceDetail(selectedSource, sourcePreviewResult),
			icon: FileText,
		};
	}
	return {
		title: "Run state",
		detail: `${bootstrap.stage_cards.length} stages`,
		icon: Activity,
	};
}

function skillResourceTitle(resource: SkillResourceEntry | null) {
	if (!resource) return "No skill file selected";
	return isMarkdownSkillResource(resource) ? "Markdown Viewer" : resource.label;
}

function skillResourceDetail(resource: SkillResourceEntry | null) {
	if (!resource) return "skill file";
	const parts = isMarkdownSkillResource(resource)
		? [resource.label, resource.skillName, resource.group]
		: [resource.skillName, resource.group];
	return parts.filter(Boolean).join(" / ");
}

function resultDetail(result: SqlResult | null) {
	if (!result) return "waiting for query";
	if (result.status === "error") return "error";
	if (result.row_count != null) return `${result.row_count} rows`;
	if (result.columns?.length) return `${result.columns.length} columns`;
	return result.summary || result.status;
}

function sourceDetail(
	source: SourceFile | null,
	previewResult: SqlResult | null,
) {
	if (!source) return "source";
	if (previewResult?.status === "error") return "preview error";
	if (previewResult?.row_count != null) {
		return `preview ${previewResult.row_count} rows`;
	}
	return source.kind || "source";
}

function targetDetail(target: Target | null, previewResult: SqlResult | null) {
	if (!target) return "target";
	if (previewResult?.status === "error") return "preview error";
	if (previewResult?.row_count != null) {
		return `preview ${previewResult.row_count} rows`;
	}
	return isTargetView(target) ? "queried view" : "extracted table";
}

function targetTitle(
	target: Target | null,
	sourceName: string | null,
	sourceFileName: string | null,
) {
	if (!target) return "No table or view selected";
	if (!isTargetView(target)) {
		return (
			<>
				Extracted Table <em className="inspector-name">{target.name}</em>
				{sourceFileName ? (
					<>
						{" "}
						From <em className="inspector-name">{sourceFileName}</em>
					</>
				) : null}
			</>
		);
	}
	return (
		<>
			Queried Result <em className="inspector-name">{target.name}</em>
			{sourceName ? (
				<>
					{" "}
					From <em className="inspector-name">{sourceName}</em>
				</>
			) : null}
		</>
	);
}

function renderHighlightedMarkdown(line: string) {
	let lineClass = "";
	if (/^\s*```/.test(line)) {
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
		/(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|_[^_]+_|\[[^\]]+\]\([^)]+\))/g,
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

function formatSkillModifiedAt(skill: SkillEntry | null) {
	if (!skill?.modified_at) {
		return "MODIFIED: -";
	}
	const modifiedAt = new Date(skill.modified_at);
	if (Number.isNaN(modifiedAt.valueOf())) {
		return "MODIFIED: -";
	}
	const year = modifiedAt.getFullYear();
	const month = String(modifiedAt.getMonth() + 1).padStart(2, "0");
	const day = String(modifiedAt.getDate()).padStart(2, "0");
	const hours = String(modifiedAt.getHours()).padStart(2, "0");
	const minutes = String(modifiedAt.getMinutes()).padStart(2, "0");
	return `MODIFIED: ${year}-${month}-${day} ${hours}:${minutes}`;
}
