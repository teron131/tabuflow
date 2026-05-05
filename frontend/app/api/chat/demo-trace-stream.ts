import type { UIMessageChunk } from "ai";

const DEMO_TRACE_DELAYS = {
	fast: 50,
	slow: 1000,
} as const;
const DEFAULT_DEMO_TRACE_MESSAGE = "show all demo trace steps";
const DEMO_PREPARED_STATE = {
	database_path: "data/demo.sqlite",
	preferred_targets: ["prepared_table"],
};
const DEMO_SKILL_FILES = [
	"skills/placeholder-analysis/SKILL.md",
	"skills/placeholder-analysis/references/schema.md",
	"skills/placeholder-analysis/references/summary-query.sql",
];

type DemoToolStepBlueprint = {
	name: string;
	input: (message: string) => unknown;
	summary: string;
	stageTrace?: DemoStageTrace[];
	output?: Record<string, unknown>;
};
type DemoStageTrace = {
	id: string;
	name: string;
	status: string;
	summary: string;
};

export function parseDemoTraceCommand(
	message: string,
	mode: unknown,
): { delayMs: number; message: string } | null {
	if (mode === "fast" || mode === "slow") {
		return { delayMs: DEMO_TRACE_DELAYS[mode], message };
	}

	const match = message.match(
		/^[@/]demoTrace(?:\s+(fast|slow))?(?:\s+(.+))?$/i,
	);
	if (!match) {
		return null;
	}
	const matchedMode = match[1] === "fast" ? "fast" : "slow";
	return {
		delayMs: DEMO_TRACE_DELAYS[matchedMode],
		message: match[2]?.trim() || DEFAULT_DEMO_TRACE_MESSAGE,
	};
}

const DEMO_TOOL_STEP_BLUEPRINTS: DemoToolStepBlueprint[] = [
	{
		name: "load_skills",
		input: () => ({ names: ["placeholder-analysis", "tabular-analysis"] }),
		summary: "loaded placeholder skill descriptions for tabular analysis",
		output: { skills: ["placeholder-analysis", "tabular-analysis"] },
	},
	{
		name: "fs_list_files",
		input: () => ({
			path: "skills/placeholder-analysis",
			glob_pattern: "**/*",
			max_files: 200,
		}),
		summary: "listed SQL and schema references for placeholder-analysis",
		output: { files: DEMO_SKILL_FILES },
	},
	{
		name: "fs_read_text",
		input: () => ({ path: "skills/placeholder-analysis/SKILL.md" }),
		summary: "read placeholder mapping and saved-view requirements",
		output: {
			content:
				"---\nname: placeholder-analysis\ndescription: Build summary and entity rows from a prepared table...",
		},
	},
	{
		name: "fs_read_hashline",
		input: () => ({
			path: "skills/placeholder-analysis/references/summary-query.sql",
			start_line: 1,
			end_line: 24,
		}),
		summary: "read hashline SQL reference for summary and entity rows",
		output: {
			lines: [
				"1#6f8a07:-- Build row_type='summary' and row_type='entity' rows.",
				"2#8de589:-- Discover the entity column from table schema before grouping.",
			],
		},
	},
	{
		name: "load_skill_resources",
		input: () => ({
			skills: ["placeholder-analysis", "tabular-analysis"],
			include_references: true,
		}),
		summary: "loaded schema hints, output contract, and SQL examples",
		output: {
			status: "ok",
			resource_count: 3,
			diagnostics: [],
		},
	},
	{
		name: "prep_stage",
		input: (message) => ({
			message,
			source_paths: ["data/uploads/source_table.csv"],
			skill_refs: ["placeholder-analysis", "tabular-analysis"],
		}),
		summary: "prepared data/uploads/source_table.csv as prepared_table",
		stageTrace: [
			{
				id: "prep-1",
				name: "profile_tabular",
				status: "completed",
				summary:
					"profiled data/uploads/source_table.csv: 14,832 rows, 9 columns",
			},
			{
				id: "prep-2",
				name: "inspect_tabular",
				status: "completed",
				summary:
					"found entity_name, metric_a_usd, metric_b_usd, and period columns",
			},
			{
				id: "prep-3",
				name: "extract_tabular",
				status: "completed",
				summary: "loaded target prepared_table into data/demo.sqlite",
			},
		],
		output: {
			prepared_state: DEMO_PREPARED_STATE,
			selected_targets: ["prepared_table"],
		},
	},
	{
		name: "query_stage",
		input: (message) => ({
			message,
			prepared_state: DEMO_PREPARED_STATE,
			max_repairs: 2,
		}),
		summary: "built analysis_result with summary row and top 5 entity rows",
		stageTrace: [
			{
				id: "query-1",
				name: "write_sql",
				status: "completed",
				summary: "wrote SQL to data/sql/placeholder-analysis-demo.sql",
			},
			{
				id: "query-2",
				name: "execute_sql",
				status: "completed",
				summary: "failed with no such column: r.entity",
			},
			{
				id: "query-3",
				name: "repair_sql",
				status: "completed",
				summary: "replaced entity with entity_name from the prepared schema",
			},
			{
				id: "query-4",
				name: "execute_sql",
				status: "completed",
				summary: "returned 6 rows from repaired SQL",
			},
			{
				id: "query-5",
				name: "validate",
				status: "completed",
				summary: "accepted one summary row plus five entity rows",
			},
			{
				id: "query-6",
				name: "save_view",
				status: "completed",
				summary: "saved view analysis_result",
			},
		],
		output: {
			result: {
				columns: ["row_type", "entity_name", "metric_a_usd", "metric_b_usd"],
				rows: [
					{
						row_type: "summary",
						entity_name: null,
						metric_a_usd: 128943.72,
						metric_b_usd: 151220.64,
					},
					{
						row_type: "entity",
						entity_name: "entity_001",
						metric_a_usd: 48210.11,
						metric_b_usd: 57198.44,
					},
					{
						row_type: "entity",
						entity_name: "entity_002",
						metric_a_usd: 39104.06,
						metric_b_usd: 45562.9,
					},
				],
			},
		},
	},
	{
		name: "summarize",
		input: () => ({
			result_view: "analysis_result",
			rows_returned: 6,
		}),
		summary: "reported the summary row and five highest placeholder entities",
		output: { view_name: "analysis_result" },
	},
];

function demoToolOutput(step: DemoToolStepBlueprint, toolCallId: string) {
	return {
		status: "ok",
		mode: "demo_trace",
		content: step.summary,
		artifact: {
			tool_trace: [
				{
					id: toolCallId,
					name: step.name,
					status: "completed",
					summary: step.summary,
				},
			],
			...(step.stageTrace ? { stage_trace: step.stageTrace } : {}),
		},
		...step.output,
	};
}

function demoToolChunks(
	step: DemoToolStepBlueprint,
	message: string,
): UIMessageChunk[] {
	const toolCallId = crypto.randomUUID();
	const toolInput = step.input(message);
	return [
		{
			type: "tool-input-available",
			toolCallId,
			toolName: "backendChat",
			input: {
				tool: step.name,
				args: toolInput,
			},
		},
		{
			type: "tool-output-available",
			toolCallId,
			output: demoToolOutput(step, toolCallId),
		},
	];
}

export function buildDemoTraceChunks(message: string): UIMessageChunk[] {
	const messageId = crypto.randomUUID();
	const introTextId = crypto.randomUUID();
	const finalTextId = crypto.randomUUID();

	return [
		{ type: "start", messageId },
		{ type: "text-start", id: introTextId },
		{
			type: "text-delta",
			id: introTextId,
			delta:
				"I will run the staged demo workflow and show each backend tool step.",
		},
		{ type: "text-end", id: introTextId },
		...DEMO_TOOL_STEP_BLUEPRINTS.flatMap((step) =>
			demoToolChunks(step, message),
		),
		{ type: "text-start", id: finalTextId },
		{
			type: "text-delta",
			id: finalTextId,
			delta:
				"Demo run complete. The trace exercised skill loading, file reads, prep_stage internals, query_stage repair, validation, and analysis_result saving without calling a model.",
		},
		{ type: "text-end", id: finalTextId },
		{ type: "finish", finishReason: "stop" },
	];
}
