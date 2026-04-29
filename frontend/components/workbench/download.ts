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
