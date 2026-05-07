import {
	BrainCircuit,
	Cloud,
	FileText,
	ListTree,
	type LucideIcon,
	Play,
	Table2,
	Zap,
} from "lucide-react";
import type {
	SkillEntry,
	SkillResourcePayload,
	SourceFile,
	Target,
} from "@/lib/api";

export type CommandItemAction = "demo-trace" | "prompt" | "run-sql";

export type CommandIndexItem = {
	id: string;
	label: string;
	category: "command" | "file" | "target" | "skill";
	description: string;
	icon: LucideIcon;
	action: CommandItemAction;
	aliases?: string[];
	demoMode?: "fast" | "slow";
	sourceFiles?: string[];
	prompt?: (detail: string) => string;
};

export type CommandIndexInput = {
	sourceFiles: SourceFile[];
	targets: Target[];
	skills: SkillEntry[];
};

export type SearchField = {
	value?: string | null;
	weight: number;
};

type NormalizedSearchField = {
	compactValue: string;
	normalizedValue: string;
	rawValue: string;
	weight: number;
};

const staticCommandItems: CommandIndexItem[] = [
	{
		id: "command-demo-trace",
		label: "demoTrace",
		category: "command",
		description: "Run staged tool trace",
		icon: Cloud,
		action: "demo-trace",
		demoMode: "slow",
	},
	{
		id: "command-demo-trace-fast",
		label: "demoTraceFast",
		category: "command",
		description: "Run fast staged tool trace",
		icon: Zap,
		action: "demo-trace",
		demoMode: "fast",
	},
	{
		id: "command-run-sql",
		label: "runSql",
		category: "command",
		description: "Run the current SQL buffer",
		icon: Play,
		action: "run-sql",
		aliases: ["execute", "query"],
	},
	{
		id: "command-list-files",
		label: "listFiles",
		category: "command",
		description: "Ask the agent for loaded files and source status",
		icon: ListTree,
		action: "prompt",
		aliases: ["sources", "uploads"],
		prompt: (detail) =>
			[
				"List the loaded workspace files, their status, and which ones are queryable.",
				detail,
			]
				.filter(Boolean)
				.join("\n\n"),
	},
	{
		id: "command-list-targets",
		label: "listTargets",
		category: "command",
		description: "Ask the agent for SQL tables and saved views",
		icon: Table2,
		action: "prompt",
		aliases: ["tables", "views"],
		prompt: (detail) =>
			[
				"List the available SQL targets with row counts and the most useful next query for each.",
				detail,
			]
				.filter(Boolean)
				.join("\n\n"),
	},
];

export function buildCommandIndex({
	sourceFiles,
	targets,
	skills,
}: CommandIndexInput): CommandIndexItem[] {
	return [
		...staticCommandItems,
		...sourceFiles.map(sourceFileItem),
		...targets.map(targetItem),
		...skills.map(skillItem),
	];
}

export function filterCommandIndex(
	items: CommandIndexItem[],
	query: string,
	limit = 64,
) {
	return filterIndexedItems(
		items,
		query,
		(item) => [
			{ value: item.label, weight: 8 },
			...(item.aliases || []).map((alias) => ({
				value: alias,
				weight: 5,
			})),
			{ value: item.category, weight: 3 },
			{ value: item.description, weight: 2 },
		],
		limit,
	);
}

export function filterIndexedItems<T>(
	items: T[],
	query: string,
	fieldsForItem: (item: T) => SearchField[],
	limit = 64,
) {
	const terms = normalizeSearchValue(query).split(/\s+/).filter(Boolean);
	if (terms.length === 0) {
		return items.slice(0, limit);
	}
	return items
		.map((item, index) => {
			const fields = fieldsForItem(item)
				.map(normalizeSearchField)
				.filter((field) => field.rawValue);
			if (fields.length === 0) {
				return null;
			}
			let score = 0;
			for (const term of terms) {
				const termScore = Math.max(
					...fields.map((field) => searchTermScore(field, term)),
				);
				if (termScore === 0) {
					return null;
				}
				score += termScore;
			}
			return { item, index, score };
		})
		.filter((result): result is { item: T; index: number; score: number } =>
			Boolean(result),
		)
		.sort((left, right) => right.score - left.score || left.index - right.index)
		.map((result) => result.item)
		.slice(0, limit);
}

function normalizeSearchValue(value: string) {
	return value
		.toLowerCase()
		.replace(/[-_\s]+/g, " ")
		.trim();
}

function normalizeSearchField(field: SearchField): NormalizedSearchField {
	const value = field.value || "";
	const normalizedValue = normalizeSearchValue(value);
	return {
		compactValue: normalizedValue.replace(/\s+/g, ""),
		normalizedValue,
		rawValue: value.toLowerCase(),
		weight: field.weight,
	};
}

function searchTermScore(field: NormalizedSearchField, term: string) {
	if (field.rawValue === term || field.normalizedValue === term) {
		return field.weight * 100;
	}
	if (
		field.rawValue.startsWith(term) ||
		field.normalizedValue.startsWith(term)
	) {
		return field.weight * 60;
	}
	if (
		field.normalizedValue.split(/[/.\s]+/).some((part) => part.startsWith(term))
	) {
		return field.weight * 40;
	}
	if (
		field.rawValue.includes(term) ||
		field.normalizedValue.includes(term) ||
		field.compactValue.includes(term)
	) {
		return field.weight * 20;
	}
	return 0;
}

export function commandDetailForItem(
	item: CommandIndexItem,
	commandText: string,
) {
	const body = commandText.replace(/^\//, "").trim();
	if (!body) {
		return "";
	}
	const aliases = [item.label, ...(item.aliases || [])];
	const matchedAlias = aliases.find((alias) =>
		body.toLowerCase().startsWith(alias.toLowerCase()),
	);
	if (!matchedAlias) {
		return "";
	}
	return body.slice(matchedAlias.length).trim();
}

function sourceFileItem(source: SourceFile): CommandIndexItem {
	const reference =
		source.source_path || source.destination_path || source.name;
	const status = [source.kind, source.status].filter(Boolean).join(" / ");
	return {
		id: `file-${source.id || reference}`,
		label: source.name,
		category: "file",
		description: status || reference,
		icon: FileText,
		action: "prompt",
		aliases: [
			reference,
			source.table_name || "",
			source.sheet_name || "",
		].filter(Boolean),
		sourceFiles: reference ? [reference] : [],
		prompt: (detail) =>
			[
				`Inspect source file ${reference}.`,
				detail || "Summarize what it contains and the next useful data action.",
			].join("\n\n"),
	};
}

function targetItem(target: Target): CommandIndexItem {
	const noun =
		target.type === "view" || target.kind.includes("view")
			? "saved view"
			: "SQL target";
	const count =
		target.row_count == null ? "unknown rows" : `${target.row_count} rows`;
	return {
		id: `target-${target.name}`,
		label: target.name,
		category: "target",
		description: `${noun} / ${count}`,
		icon: Table2,
		action: "prompt",
		aliases: [target.type, target.kind, target.summary].filter(Boolean),
		sourceFiles: target.source_references?.map((reference) => reference.path),
		prompt: (detail) =>
			[
				`Inspect ${noun} ${target.name}.`,
				detail ||
					"Describe the schema, preview the useful rows, and suggest a next query.",
			].join("\n\n"),
	};
}

function skillItem(skill: SkillEntry): CommandIndexItem {
	const packagePaths = skillPackagePaths(skill);
	const packageSummary = skillPackageSummary(skill);
	return {
		id: `skill-${skill.name}`,
		label: skill.name,
		category: "skill",
		description: packageSummary,
		icon: BrainCircuit,
		action: "prompt",
		aliases: [
			skill.description || "",
			skill.path || "",
			skill.skills_path || "",
			packageSummary,
			...packagePaths,
		].filter(Boolean),
		prompt: (detail) =>
			[
				`Load the ${skill.name} workspace skill package and apply it.`,
				packagePaths.length
					? `Package files:\n${packagePaths.map((path) => `- ${path}`).join("\n")}`
					: "",
				detail ||
					"Use its instructions, references, scripts, and examples as one skill package.",
			].join("\n\n"),
	};
}

function skillPackagePaths(skill: SkillEntry) {
	return [
		skill.instructions?.relative_path ||
			skill.instructions?.path ||
			skill.path ||
			"",
		...skillResourcePaths(skill.examples),
		...skillResourcePaths(skill.references),
		...skillResourcePaths(skill.scripts),
	].filter(Boolean);
}

function skillResourcePaths(resources: SkillResourcePayload[] | undefined) {
	return (resources || [])
		.map((resource) => resource.relative_path || resource.path || "")
		.filter(Boolean);
}

function skillPackageSummary(skill: SkillEntry) {
	const counts = [
		skill.examples?.length ? `${skill.examples.length} examples` : "",
		skill.references?.length ? `${skill.references.length} references` : "",
		skill.scripts?.length ? `${skill.scripts.length} scripts` : "",
	].filter(Boolean);
	return counts.length
		? `Skill package / ${counts.join(" / ")}`
		: "Skill package";
}
