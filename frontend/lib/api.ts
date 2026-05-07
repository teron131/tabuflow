export type HealthPayload = {
	status: string;
	model: string;
	llm_configured: boolean;
	database_ready: boolean;
};

export type SqlArtifact = {
	name: string;
	type: string;
	kind: string;
	row_count: number | null;
	column_count?: number;
	size_label?: string;
	source_path_count: number;
	source_file_names?: string[];
	source_references?: SourceReference[];
	source_sql_artifact_names?: string[];
	summary: string;
};

export type SqlArtifactDetails = SqlArtifact & {
	create_sql?: string;
};

export type SourceReference = {
	name: string;
	path: string;
	format: string;
	sheet_name: string;
	table_name: string;
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
	sql_artifacts: SqlArtifact[];
	sql_artifact_summary: string;
	initial_result?: SqlResult;
};

export type UploadPayload = {
	status: string;
	upload?: {
		status?: string;
		name?: string;
		path?: string;
		format?: string;
		artifact_backend?: string;
		content_type?: string;
		size_bytes?: number;
		recovered_table_count?: number;
		page_count?: number;
	};
	bootstrap?: BootstrapPayload;
	detail?: {
		message?: string;
	};
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

export type SkillResourcePayload = {
	path?: string;
	relative_path?: string;
	kind?: string;
	content?: string;
};

export type SkillResourceEntry = SkillResourcePayload & {
	skillName: string;
	label: string;
	group: "instructions" | "examples" | "references" | "scripts";
};

export type FileExplanation = {
	status: string;
	path: string;
	relative_path: string;
	content_hash: string;
	summary: string;
	model: string;
	generated_at: string;
	cached: boolean;
};

export type SkillResourceSavePayload = {
	status: string;
	path: string;
	relative_path: string;
	content: string;
	modified_at?: string | null;
	summary?: string;
};

export type SkillEntry = {
	name: string;
	description?: string;
	path?: string;
	skills_path?: string;
	modified_at?: string | null;
	content?: string;
	instructions?: {
		path?: string;
		relative_path?: string;
		content?: string;
	};
	references?: SkillResourcePayload[];
	scripts?: SkillResourcePayload[];
	examples?: SkillResourcePayload[];
};

export const emptyBootstrap: BootstrapPayload = {
	status: "loading",
	sample_sql: `SELECT
  'ready' AS status,
  'Select a source, table, or saved result to inspect.' AS message;`,
	suggested_questions: [
		"What sources are loaded?",
		"Show available SQL artifacts.",
		"Preview the selected result.",
	],
	stage_cards: [
		{ name: "Prep", status: "ready", summary: "Inspect files." },
		{ name: "Query", status: "ready", summary: "Run bounded SQL." },
		{ name: "Save", status: "ready", summary: "Persist useful views." },
	],
	source_files: [],
	sql_artifacts: [],
	sql_artifact_summary: "",
};

export async function fetchJson<T>(
	url: string,
	init?: RequestInit,
): Promise<T> {
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

export async function uploadWorkspaceFile(file: File): Promise<UploadPayload> {
	const formData = new FormData();
	formData.append("file", file);
	const response = await fetch("/api/files/upload", {
		method: "POST",
		body: formData,
	});
	const payload = (await response.json().catch(() => ({
		status: "error",
		detail: { message: "Upload returned a non-JSON response." },
	}))) as UploadPayload;
	if (!response.ok) {
		throw new Error(
			payload.detail?.message || `${response.status} ${response.statusText}`,
		);
	}
	return payload;
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

export async function explainWorkspaceFile({
	path,
	force = false,
	model,
}: {
	path: string;
	force?: boolean;
	model?: string;
}): Promise<FileExplanation> {
	return fetchJson<FileExplanation>("/api/explainer/summary", {
		method: "POST",
		body: JSON.stringify({ path, force, model }),
	});
}

export async function saveSkillResource({
	path,
	content,
}: {
	path: string;
	content: string;
}): Promise<SkillResourceSavePayload> {
	return fetchJson<SkillResourceSavePayload>("/api/skills/resource/save", {
		method: "POST",
		body: JSON.stringify({ path, content }),
	});
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
