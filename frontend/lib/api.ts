export type HealthPayload = {
	status: string;
	model: string;
	llm_configured: boolean;
	database_ready: boolean;
};

export type Target = {
	name: string;
	type: string;
	kind: string;
	row_count: number | null;
	column_count?: number;
	size_label?: string;
	source_path_count: number;
	summary: string;
};

export type SourceFile = {
	id: string;
	name: string;
	kind: string;
	status: string;
	source_path?: string;
	destination_path?: string;
	sheet_name?: string;
	table_name?: string;
};

export type BootstrapPayload = {
	status: string;
	sample_sql: string;
	suggested_questions: string[];
	stage_cards: Array<{ name: string; status: string; summary: string }>;
	source_files: SourceFile[];
	targets: Target[];
	target_summary: string;
	initial_result?: SqlResult;
};

export type SqlResult = {
	status: string;
	summary?: string;
	columns?: string[];
	rows?: Array<Record<string, unknown>>;
	row_count?: number;
	truncated?: boolean;
	error_type?: string;
	message?: string;
};

export type SkillEntry = {
	name: string;
	description?: string;
	path?: string;
	skills_path?: string;
	content?: string;
	instructions?: {
		path?: string;
		relative_path?: string;
		content?: string;
	};
	references?: Array<{ relative_path?: string; content?: string }>;
	scripts?: Array<{ relative_path?: string }>;
};

export const emptyBootstrap: BootstrapPayload = {
	status: "loading",
	sample_sql: `SELECT
  metric,
  billing_account_name,
  grand_total_cost_usd,
  total_unrounded_cost_usd,
  rank_n
FROM analysis_result
LIMIT 10;`,
	suggested_questions: [
		"Show the grand total cost.",
		"Rank billing accounts by cost.",
		"Explain the top account.",
	],
	stage_cards: [
		{ name: "Prep", status: "ready", summary: "Inspect files." },
		{ name: "Query", status: "ready", summary: "Run bounded SQL." },
		{ name: "Save", status: "ready", summary: "Persist useful views." },
	],
	source_files: [],
	targets: [],
	target_summary: "",
};

export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
	const response = await fetch(url, {
		headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
		...init,
	});
	if (!response.ok) {
		const text = await response.text();
		throw new Error(text || `${response.status} ${response.statusText}`);
	}
	return response.json() as Promise<T>;
}

export function skillContent(skill: SkillEntry): string {
	return (
		skill.content ||
		skill.instructions?.content ||
		`Use this skill when ${skill.description || "the workflow needs focused instructions."}`
	);
}

export function skillLineCount(skill: SkillEntry): number {
	const content = skill.content || skill.instructions?.content || "";
	const trimmedContent = content.trimEnd();
	if (!trimmedContent) {
		return 0;
	}
	return trimmedContent.split(/\r\n|\r|\n/).length;
}

export function toCsv(result: SqlResult): string {
	const columns = result.columns || [];
	const rows = result.rows || [];
	const escapeCell = (value: unknown) => {
		const text = value == null ? "" : String(value);
		return `"${text.replaceAll('"', '""')}"`;
	};
	return [
		columns.map(escapeCell).join(","),
		...rows.map((row) =>
			columns.map((column) => escapeCell(row[column])).join(","),
		),
	].join("\n");
}
