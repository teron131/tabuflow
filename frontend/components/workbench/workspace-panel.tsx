import { Download, Play, RotateCcw, Save } from "lucide-react";
import type {
	ReactNode,
	PointerEvent as ReactPointerEvent,
	RefObject,
} from "react";
import { useRef } from "react";
import {
	type BootstrapPayload,
	type SkillEntry,
	type SourceFile,
	type SqlResult,
	skillLineCount,
	type Target,
} from "@/lib/api";
import { targetBadge } from "./badges";
import { workspaceTabs } from "./constants";
import { downloadResult } from "./download";
import { ResultTable } from "./result-table";
import { renderHighlightedSql } from "./sql";
import type { CenterTab } from "./types";

type WorkspacePanelProps = {
	activeTab: CenterTab;
	bootstrap: BootstrapPayload;
	centerRef: RefObject<HTMLElement | null>;
	isRunningSql: boolean;
	isSkillSaved: boolean;
	selectedSkill: SkillEntry | null;
	selectedSource: SourceFile | null;
	selectedTarget: Target | null;
	skillText: string;
	sql: string;
	sqlResult: SqlResult | null;
	onActiveTabChange: (tab: CenterTab) => void;
	onRevertSkill: () => void;
	onRunSql: () => void;
	onSaveSkill: () => void;
	onSkillTextChange: (text: string) => void;
	onSqlChange: (sql: string) => void;
	onVerticalResize: (event: ReactPointerEvent) => void;
};

export function WorkspacePanel({
	activeTab,
	bootstrap,
	centerRef,
	isRunningSql,
	isSkillSaved,
	selectedSkill,
	selectedSource,
	selectedTarget,
	skillText,
	sql,
	sqlResult,
	onActiveTabChange,
	onRevertSkill,
	onRunSql,
	onSaveSkill,
	onSkillTextChange,
	onSqlChange,
	onVerticalResize,
}: WorkspacePanelProps) {
	const sqlEditorRef = useRef<HTMLDivElement | null>(null);
	const sqlHighlightRef = useRef<HTMLPreElement | null>(null);
	const skillEditorRef = useRef<HTMLDivElement | null>(null);
	const skillHighlightRef = useRef<HTMLPreElement | null>(null);

	function updateSqlCursor(editor: HTMLTextAreaElement) {
		updateEditorActiveLine(
			editor,
			sqlEditorRef.current,
			sqlHighlightRef.current,
		);
	}

	function updateSkillCursor(editor: HTMLTextAreaElement) {
		updateEditorActiveLine(
			editor,
			skillEditorRef.current,
			skillHighlightRef.current,
		);
	}

	return (
		<section ref={centerRef} className="workspace">
			<header className="workspace-tabs">
				{workspaceTabs.map((tab) => {
					const Icon = tab.icon;
					return (
						<button
							key={tab.key}
							className={activeTab === tab.key ? "active" : ""}
							onClick={() => onActiveTabChange(tab.key)}
							type="button"
						>
							<Icon size={14} />
							{tab.label}
						</button>
					);
				})}
			</header>

			<div className="workspace-grid">
				<section className="query-pane">
					<header className="pane-header">
						<span>QUERY BUFFER</span>
						<div className="button-cluster">
							<button
								className="outline-button"
								type="button"
								onClick={() => onSqlChange(bootstrap.sample_sql)}
							>
								Reset
							</button>
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
					<div ref={sqlEditorRef} className="sql-editor-wrap">
						<div className="editor-active-line" aria-hidden="true" />
						<pre
							ref={sqlHighlightRef}
							aria-hidden="true"
							className="editor-content sql-highlight"
						>
							{editorLines(sql).map((line, index) => (
								<span className="editor-line" key={line.key}>
									<span className="editor-line-number">{index + 1}</span>
									<span className="editor-line-text">
										{line.text ? renderHighlightedSql(line.text) : "\u00a0"}
									</span>
								</span>
							))}
						</pre>
						<textarea
							aria-label="SQL editor"
							value={sql}
							onChange={(event) => {
								onSqlChange(event.target.value);
								updateSqlCursor(event.currentTarget);
							}}
							onClick={(event) => updateSqlCursor(event.currentTarget)}
							onBlur={() => clearEditorActiveLine(sqlEditorRef.current)}
							onKeyDown={(event) => {
								const editor = event.currentTarget;
								requestAnimationFrame(() => updateSqlCursor(editor));
							}}
							onKeyUp={(event) => updateSqlCursor(event.currentTarget)}
							onPointerDown={() => clearEditorActiveLine(sqlEditorRef.current)}
							onScroll={(event) => {
								const editor = event.currentTarget;
								syncEditorScroll(editor, sqlHighlightRef.current);
								updateSqlCursor(editor);
							}}
							onSelect={(event) => updateSqlCursor(event.currentTarget)}
							spellCheck={false}
							wrap="soft"
						/>
					</div>
				</section>

				<button
					className="resize-handle vertical-handle"
					onPointerDown={onVerticalResize}
					type="button"
					aria-label="Resize query pane"
				/>

				<section
					className={
						activeTab === "results"
							? "output-pane result-output"
							: "output-pane"
					}
				>
					<header className="pane-header">
						<span>{activeTab.toUpperCase()}</span>
						{activeTab === "results" && sqlResult?.columns?.length ? (
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
					<div
						className={
							activeTab === "results"
								? "pane-body result-pane-body"
								: "pane-body"
						}
					>
						{activeTab === "sql" && (
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
						{activeTab === "results" && <ResultTable result={sqlResult} />}
						{activeTab === "target" && (
							<div className="detail-panel">
								<h3>{selectedTarget?.name || "No target selected"}</h3>
								<p>{selectedTarget?.summary || bootstrap.target_summary}</p>
								<dl>
									<dt>kind</dt>
									<dd>
										{selectedTarget ? targetBadge(selectedTarget.kind) : "-"}
									</dd>
									<dt>size</dt>
									<dd>{selectedTarget?.size_label || "-"}</dd>
									<dt>rows</dt>
									<dd>{selectedTarget?.row_count ?? "-"}</dd>
									<dt>columns</dt>
									<dd>{selectedTarget?.column_count ?? "-"}</dd>
									<dt>sources</dt>
									<dd>{selectedTarget?.source_path_count ?? "-"}</dd>
								</dl>
							</div>
						)}
						{activeTab === "skill" && (
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
								<div ref={skillEditorRef} className="skill-editor-wrap">
									<div className="editor-active-line" aria-hidden="true" />
									<pre
										ref={skillHighlightRef}
										aria-hidden="true"
										className="editor-content skill-highlight"
									>
										{editorLines(skillText).map((line, index) => (
											<span className="editor-line" key={line.key}>
												<span className="editor-line-number">{index + 1}</span>
												<span className="editor-line-text">
													{line.text
														? renderHighlightedMarkdown(line.text)
														: "\u00a0"}
												</span>
											</span>
										))}
									</pre>
									<textarea
										value={skillText}
										onChange={(event) => {
											onSkillTextChange(event.target.value);
											updateSkillCursor(event.currentTarget);
										}}
										onClick={(event) => updateSkillCursor(event.currentTarget)}
										onBlur={() => clearEditorActiveLine(skillEditorRef.current)}
										onKeyDown={(event) => {
											const editor = event.currentTarget;
											requestAnimationFrame(() => updateSkillCursor(editor));
										}}
										onKeyUp={(event) => updateSkillCursor(event.currentTarget)}
										onPointerDown={() =>
											clearEditorActiveLine(skillEditorRef.current)
										}
										onScroll={(event) => {
											const editor = event.currentTarget;
											syncEditorScroll(editor, skillHighlightRef.current);
											updateSkillCursor(editor);
										}}
										onSelect={(event) => updateSkillCursor(event.currentTarget)}
										spellCheck={false}
										wrap="soft"
									/>
								</div>
							</div>
						)}
						{activeTab === "source" && (
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
				</section>
			</div>
		</section>
	);
}

function editorLines(text: string) {
	const matches = text.matchAll(/[^\r\n]*(?:\r\n|\r|\n|$)/g);
	const lines: Array<{ key: string; text: string }> = [];
	for (const match of matches) {
		if (match.index === text.length && match[0] === "") {
			continue;
		}
		lines.push({
			key: `line-${match.index}`,
			text: match[0].replace(/\r\n|\r|\n$/, ""),
		});
	}
	return lines.length ? lines : [{ key: "line-0", text: "" }];
}

function updateEditorActiveLine(
	editor: HTMLTextAreaElement,
	container: HTMLDivElement | null,
	contentLayer: HTMLPreElement | null,
) {
	if (!container || !contentLayer) {
		return;
	}
	const lineNumber = editor.value
		.slice(0, editor.selectionStart)
		.split(/\r\n|\r|\n/).length;
	const activeLine = contentLayer.children.item(lineNumber - 1);
	if (!(activeLine instanceof HTMLElement)) {
		clearEditorActiveLine(container);
		return;
	}
	const activeLineTop = activeLine.offsetTop - contentLayer.scrollTop;
	container.dataset.activeLine = "true";
	container.style.setProperty("--editor-active-line-top", `${activeLineTop}px`);
	container.style.setProperty(
		"--editor-active-line-height",
		`${activeLine.offsetHeight}px`,
	);
}

function clearEditorActiveLine(container: HTMLDivElement | null) {
	if (!container) {
		return;
	}
	delete container.dataset.activeLine;
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

function syncEditorScroll(
	editor: HTMLTextAreaElement,
	contentLayer: HTMLPreElement | null,
) {
	if (!contentLayer) {
		return;
	}
	const maxContentScroll = Math.max(
		0,
		contentLayer.scrollHeight - contentLayer.clientHeight,
	);
	const nextScrollTop = Math.min(editor.scrollTop, maxContentScroll);
	if (editor.scrollTop !== nextScrollTop) {
		editor.scrollTop = nextScrollTop;
	}
	contentLayer.scrollTop = nextScrollTop;
	contentLayer.scrollLeft = editor.scrollLeft;
}
