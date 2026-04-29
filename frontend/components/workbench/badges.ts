export function fileBadge(name: string, kind = "") {
	const value = `${name} ${kind}`.toLowerCase();
	if (value.includes("sqlite")) return "DB";
	if (value.includes("csv")) return "CSV";
	if (value.includes("sql")) return "SQL";
	if (value.includes("skill") || value.includes(".md")) return "MD";
	return "FILE";
}

export function targetBadge(kind: string) {
	if (kind === "raw_content_table") return "RAW";
	if (kind === "typed_content_view") return "TYPED";
	if (kind === "view_or_table") return "VIEW";
	return kind || "SQL";
}

export function badgeTone(label: string) {
	const value = label.toLowerCase();
	if (value === "raw" || value === "csv") return "tone-raw";
	if (value === "typed" || value === "db") return "tone-typed";
	if (value === "view" || value === "sql") return "tone-view";
	if (value === "skill" || value === "md") return "tone-skill";
	return "tone-file";
}
