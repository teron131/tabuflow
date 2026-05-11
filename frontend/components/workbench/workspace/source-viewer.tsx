import type { SourceFile, SourceTargetProfile, SqlResult } from "@/lib/api";
import type { RoundingSettings, ThemeMode } from "../types";
import { ResultTable } from "./result-table";

export function SourceViewer({
	isPreviewingSource,
	rounding,
	selectedSource,
	sourcePreviewResult,
	themeMode,
}: {
	isPreviewingSource: boolean;
	rounding: RoundingSettings;
	selectedSource: SourceFile | null;
	sourcePreviewResult: SqlResult | null;
	themeMode: ThemeMode;
}) {
	if (isPreviewingSource) {
		return (
			<div className="loading-state">
				<strong>Loading preview</strong>
				<div className="state-lines" aria-hidden="true">
					<i />
					<i />
					<i />
				</div>
			</div>
		);
	}
	const targets = selectedSource?.targets ?? [];
	const targetCount = selectedSource?.target_count ?? targets.length;
	const hasTargets = targets.length > 0;
	if (sourcePreviewResult) {
		return (
			<div className="data-preview-viewer">
				{selectedSource ? (
					<section className="schema-profile-strip">
						<strong>{selectedSource.name}</strong>
						<span>{selectedSource.kind}</span>
						<span>{selectedSource.status}</span>
						<span>{targetCount} targets</span>
					</section>
				) : null}
				<div className="data-preview-grid">
					<ResultTable
						result={sourcePreviewResult}
						rounding={rounding}
						themeMode={themeMode}
					/>
				</div>
				{hasTargets ? <TargetProfiles targets={targets} /> : null}
			</div>
		);
	}
	return (
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
				<dt>targets</dt>
				<dd>{targetCount}</dd>
			</dl>
			{hasTargets ? <TargetProfiles targets={targets} /> : null}
		</div>
	);
}

function TargetProfiles({ targets }: { targets: SourceTargetProfile[] }) {
	if (!targets.length) {
		return null;
	}
	return (
		<section className="target-profile-list" aria-label="Extracted targets">
			{targets.map((target) => (
				<article className="target-profile-card" key={target.name}>
					<header>
						<strong>{target.name}</strong>
						<span>{target.size_label || target.kind || target.type}</span>
					</header>
					<p>
						{target.summary || "Queryable target extracted from this source."}
					</p>
					{target.columns?.length ? (
						<div className="column-chip-row">
							{target.columns.slice(0, 8).map((column) => (
								<span key={`${target.name}-${column.name}`}>{column.name}</span>
							))}
						</div>
					) : null}
				</article>
			))}
		</section>
	);
}
