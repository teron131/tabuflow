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

type DemoStageToolBlueprint = {
	name: string;
	input: (message: string) => unknown;
	summary: string;
	stageTrace: DemoStageTrace[];
	visiblePayload: Record<string, unknown>;
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

const DEMO_STAGE_TOOL_BLUEPRINTS: DemoStageToolBlueprint[] = [
	{
		name: "prep_stage",
		input: (message) => ({
			message,
			source_files: ["data/uploads/source_table.csv"],
			max_validation_retries: 2,
		}),
		summary: "prepared data/uploads/source_table.csv as prepared_table",
		stageTrace: [
			{
				id: "prep-1",
				name: "load_skills",
				status: "completed",
				summary: "loaded placeholder skill descriptions for tabular analysis",
			},
			{
				id: "prep-2",
				name: "fs_list_files",
				status: "completed",
				summary: "listed SQL and schema references for placeholder-analysis",
			},
			{
				id: "prep-3",
				name: "fs_read_text",
				status: "completed",
				summary: "read placeholder mapping and saved-view requirements",
			},
			{
				id: "prep-4",
				name: "fs_read_hashline",
				status: "completed",
				summary: "read hashline SQL reference for summary and entity rows",
			},
			{
				id: "prep-5",
				name: "load_skill_resources",
				status: "completed",
				summary: "loaded schema hints, output contract, and SQL examples",
			},
			{
				id: "prep-6",
				name: "profile_tabular",
				status: "completed",
				summary:
					"profiled data/uploads/source_table.csv: 14,832 rows, 9 columns",
			},
			{
				id: "prep-7",
				name: "inspect_tabular",
				status: "completed",
				summary:
					"found entity_name, metric_a_usd, metric_b_usd, and period columns",
			},
			{
				id: "prep-8",
				name: "extract_tabular",
				status: "completed",
				summary: "loaded target prepared_table into data/demo.sqlite",
			},
		],
		visiblePayload: {
			status: "prepared",
			database_path: "data/demo.sqlite",
			prepared_state_available: true,
			target_count: 1,
			preferred_targets: ["prepared_table"],
			trace: [
				"skill_context: load_skills: loaded placeholder skill descriptions for tabular analysis",
				"skill_context: fs_list_files: listed SQL and schema references for placeholder-analysis",
				"skill_context: fs_read_text: read placeholder mapping and saved-view requirements",
				"skill_context: fs_read_hashline: read hashline SQL reference for summary and entity rows",
				"skill_context: load_skill_resources: loaded schema hints, output contract, and SQL examples",
				"prep: profile_tabular: profiled data/uploads/source_table.csv: 14,832 rows, 9 columns",
				"prep: inspect_tabular: found entity_name, metric_a_usd, metric_b_usd, and period columns",
				"prep: extract_tabular: loaded target prepared_table into data/demo.sqlite",
			],
			skill_files: DEMO_SKILL_FILES,
			prepared_state: DEMO_PREPARED_STATE,
			selected_targets: ["prepared_table"],
		},
	},
	{
		name: "query_stage",
		input: (message) => ({
			message,
			max_validation_retries: 2,
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
		visiblePayload: {
			status: "saved",
			outcome: "fulfilled",
			completion_reason: "saved_view",
			content:
				"Saved analysis_result with one summary row and five highest placeholder entities.",
			saved_view_name: "analysis_result",
			sql_path: "data/sql/placeholder-analysis-demo.sql",
			trace: [
				"query: write_sql: wrote SQL to data/sql/placeholder-analysis-demo.sql",
				"query: execute_sql: failed with no such column: r.entity",
				"query: repair_sql: replaced entity with entity_name from the prepared schema",
				"query: execute_sql: returned 6 rows from repaired SQL",
				"query: validate: accepted one summary row plus five entity rows",
				"query: save_view: saved view analysis_result",
			],
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
];

function demoToolOutput(step: DemoStageToolBlueprint, toolCallId: string) {
	return {
		status: "ok",
		mode: "model_stream",
		content: JSON.stringify(step.visiblePayload),
		artifact: {
			tool_trace: [
				{
					id: toolCallId,
					name: step.name,
					status: "completed",
					summary: step.summary,
				},
			],
			stage_trace: step.stageTrace,
		},
	};
}

function demoToolChunks(
	step: DemoStageToolBlueprint,
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
				"I will run the staged demo workflow and promote the inner stage trace.",
		},
		{ type: "text-end", id: introTextId },
		...DEMO_STAGE_TOOL_BLUEPRINTS.flatMap((step) =>
			demoToolChunks(step, message),
		),
		{ type: "text-start", id: finalTextId },
		{
			type: "text-delta",
			id: finalTextId,
			delta:
				"Demo run complete. The transport used prep_stage and query_stage wrapper events, while the UI rendered the nested stage_trace rows as top-level tool cards.",
		},
		{ type: "text-end", id: finalTextId },
		{ type: "finish", finishReason: "stop" },
	];
}
