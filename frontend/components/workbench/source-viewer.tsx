import type { SourceFile, SqlResult } from "@/lib/api";
import { ResultTable } from "./result-table";
import type { RoundingSettings } from "./types";

export function SourceViewer({
	isPreviewingSource,
	rounding,
	selectedSource,
	sourcePreviewResult,
}: {
	isPreviewingSource: boolean;
	rounding: RoundingSettings;
	selectedSource: SourceFile | null;
	sourcePreviewResult: SqlResult | null;
}) {
	if (isPreviewingSource) {
		return <div className="empty-state">Loading preview</div>;
	}
	if (sourcePreviewResult) {
		return (
			<div className="target-viewer">
				<div className="target-preview-grid">
					<ResultTable result={sourcePreviewResult} rounding={rounding} />
				</div>
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
			</dl>
		</div>
	);
}
