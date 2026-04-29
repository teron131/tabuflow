import { Download, Play, Save } from "lucide-react";
import type { PointerEvent as ReactPointerEvent, RefObject } from "react";
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
	selectedSkill: SkillEntry | null;
	selectedSource: SourceFile | null;
	selectedTarget: Target | null;
	skillDraft: string;
	sql: string;
	sqlResult: SqlResult | null;
	onActiveTabChange: (tab: CenterTab) => void;
	onRunSql: () => void;
	onSkillDraftChange: (draft: string) => void;
	onSqlChange: (sql: string) => void;
	onVerticalResize: (event: ReactPointerEvent) => void;
};

export function WorkspacePanel({
	activeTab,
	bootstrap,
	centerRef,
	isRunningSql,
	selectedSkill,
	selectedSource,
	selectedTarget,
	skillDraft,
	sql,
	sqlResult,
	onActiveTabChange,
	onRunSql,
	onSkillDraftChange,
	onSqlChange,
	onVerticalResize,
}: WorkspacePanelProps) {
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
					<div className="sql-editor-wrap">
						<pre aria-hidden="true" className="sql-highlight">
							{renderHighlightedSql(sql)}
						</pre>
						<textarea
							aria-label="SQL editor"
							value={sql}
							onChange={(event) => onSqlChange(event.target.value)}
							spellCheck={false}
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
									<h3>{selectedSkill?.name || "No skill selected"}</h3>
									<span className="skill-line-count">
										{selectedSkill ? skillLineCount(selectedSkill) : "-"}
									</span>
									<button className="outline-button" type="button">
										<Save size={13} />
										Draft
									</button>
								</header>
								<textarea
									value={skillDraft}
									onChange={(event) => onSkillDraftChange(event.target.value)}
									spellCheck={false}
								/>
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
