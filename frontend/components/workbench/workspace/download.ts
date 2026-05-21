import type { SqlResult } from "@/lib/api";
import { toCsv } from "@/lib/api";

export function downloadResult(
	result: SqlResult,
	filename = "tabuflow-result.csv",
) {
	const blob = new Blob([toCsv(result)], { type: "text/csv;charset=utf-8" });
	const url = URL.createObjectURL(blob);
	const anchor = document.createElement("a");
	anchor.href = url;
	anchor.download = filename;
	anchor.click();
	URL.revokeObjectURL(url);
}

export function downloadSqlArtifactView(sqlArtifactName: string) {
	const anchor = document.createElement("a");
	anchor.href = `/api/sql/sql-artifacts/${encodeURIComponent(sqlArtifactName)}/download`;
	anchor.download = `${safeCsvFilename(sqlArtifactName)}.csv`;
	anchor.click();
}

function safeCsvFilename(value: string) {
	return (
		value.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[.-]+|[.-]+$/g, "") ||
		"view"
	);
}
