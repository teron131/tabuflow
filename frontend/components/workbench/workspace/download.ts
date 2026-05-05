import type { SqlResult } from "@/lib/api";
import { toCsv } from "@/lib/api";

export function downloadResult(
	result: SqlResult,
	filename = "data-agentics-result.csv",
) {
	const blob = new Blob([toCsv(result)], { type: "text/csv;charset=utf-8" });
	const url = URL.createObjectURL(blob);
	const anchor = document.createElement("a");
	anchor.href = url;
	anchor.download = filename;
	anchor.click();
	URL.revokeObjectURL(url);
}

export function downloadTargetView(targetName: string) {
	const anchor = document.createElement("a");
	anchor.href = `/api/sql/targets/${encodeURIComponent(targetName)}/download`;
	anchor.download = `${safeCsvFilename(targetName)}.csv`;
	anchor.click();
}

function safeCsvFilename(value: string) {
	return (
		value.replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^[.-]+|[.-]+$/g, "") ||
		"view"
	);
}
