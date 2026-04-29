import {
	Activity,
	ChevronDown,
	ChevronRight,
	Download,
	FileCode2,
	FileText,
	type LucideIcon,
	Play,
	RotateCcw,
	Save,
	ScrollText,
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
	type SourceFile,
	type SqlResult,
	skillLineCount,
	type Target,
} from "@/lib/api";
import { CodeEditor } from "./code-editor";
import { downloadResult } from "./download";
import { ResultTable } from "./result-table";
import { renderHighlightedSql } from "./sql";
import { isTargetView } from "./targets";
import type { InspectorView, RoundingSettings } from "./types";

type CollapsiblePane = "query" | "output";

type WorkspacePanelProps = {
	bootstrap: BootstrapPayload;
	centerRef: RefObject<HTMLElement | null>;
	inspectorView: InspectorView;
	isPreviewingTarget: boolean;
	isRunningSql: boolean;
	isSkillSaved: boolean;
	rounding: RoundingSettings;
	selectedSkill: SkillEntry | null;
	selectedSource: SourceFile | null;
	selectedTarget: Target | null;
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
	isRunningSql,
	isSkillSaved,
	rounding,
	selectedSkill,
	selectedSource,
	selectedTarget,
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
	const [queryCollapsed, setQueryCollapsed] = useState(false);
	const [outputCollapsed, setOutputCollapsed] = useState(false);
	const renderSqlLine = useCallback(
		(line: string) => renderHighlightedSql(line),
		[],
	);
	const renderSkillLine = useCallback(
		(line: string) => renderHighlightedMarkdown(line),
		[],
	);
	const togglePane = useCallback((pane: CollapsiblePane) => {
		if (pane === "query") {
			setQueryCollapsed((collapsed) => !collapsed);
			return;
		}
		setOutputCollapsed((collapsed) => !collapsed);
	}, []);
	const inspector = inspectorState({
		bootstrap,
		inspectorView,
		selectedSkill,
		selectedSource,
		selectedTarget,
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
					queryCollapsed ? "query-collapsed" : "",
					outputCollapsed ? "output-collapsed" : "",
				]
					.filter(Boolean)
					.join(" ")}
			>
				<section className="query-pane">
					<header className="pane-header collapsible-pane-header">
						<button
							aria-controls="query-pane-body"
							aria-expanded={!queryCollapsed}
							className="pane-toggle"
							onClick={() => togglePane("query")}
							type="button"
						>
							{queryCollapsed ? (
								<ChevronRight size={14} aria-hidden="true" />
							) : (
								<ChevronDown size={14} aria-hidden="true" />
							)}
							QUERY BUFFER
						</button>
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
					</header>
					{!queryCollapsed ? (
						<div id="query-pane-body" className="query-pane-body">
							<CodeEditor
								ariaLabel="SQL editor"
								className="sql-editor-wrap"
								highlightClassName="sql-highlight"
								renderLine={renderSqlLine}
								value={sql}
								onChange={onSqlChange}
							/>
						</div>
					) : null}
				</section>

				{!queryCollapsed && !outputCollapsed ? (
					<button
						className="resize-handle vertical-handle"
						onPointerDown={onVerticalResize}
						type="button"
						aria-label="Resize query pane"
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
								inspectorView === "results"
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
							{inspectorView === "source" && (
								<div className="detail-panel">
									<h3>{selectedSource?.name || "No source selected"}</h3>
									<p>
										{selectedSource
											? `${selectedSource.kind} source is ${selectedSource.status}.`
											: "Pick a source in Explorer."}
									</p>
									<dl>
										<dt>type</dt>
										<dd>{selectedSource?.kind || "-"}</dd>
										<dt>source</dt>
										<dd>{selectedSource?.source_path || "-"}</dd>
										<dt>destination</dt>
										<dd>{selectedSource?.destination_path || "-"}</dd>
										<dt>table</dt>
										<dd>{selectedSource?.table_name || "-"}</dd>
									</dl>
								</div>
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
	selectedSource,
	selectedTarget,
	sqlResult,
	targetPreviewResult,
	targetSourceFileName,
	targetSourceName,
}: {
	bootstrap: BootstrapPayload;
	inspectorView: InspectorView;
	selectedSkill: SkillEntry | null;
	selectedSource: SourceFile | null;
	selectedTarget: Target | null;
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
	if (inspectorView === "source") {
		return {
			title: selectedSource?.name || "No source selected",
			detail: selectedSource?.kind || "source",
			icon: FileText,
		};
	}
	return {
		title: "Run state",
		detail: `${bootstrap.stage_cards.length} stages`,
		icon: Activity,
	};
}

function resultDetail(result: SqlResult | null) {
	if (!result) return "waiting for query";
	if (result.status === "error") return "error";
	if (result.row_count != null) return `${result.row_count} rows`;
	if (result.columns?.length) return `${result.columns.length} columns`;
	return result.summary || result.status;
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
