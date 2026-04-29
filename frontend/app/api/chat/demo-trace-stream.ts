import type { UIMessageChunk } from "ai";

const DEMO_TRACE_DELAYS = {
	fast: 50,
	slow: 1000,
} as const;
const DEFAULT_DEMO_TRACE_MESSAGE = "show all demo trace steps";
const DEMO_SOURCE_FILES = ["cost_table_demo.csv"];
const DEMO_PREPARED_STATE = {
	database_path: "data/tabular.sqlite",
	preferred_targets: ["content_23ac1d333f4101ab_typed"],
};

type StageTraceEntry = {
	id: string;
	name: string;
	summary: string;
};

type DemoToolStep = {
	name: string;
	input: unknown;
	output: unknown;
};
type DemoToolStepBlueprint = {
	name: string;
	input: (message: string) => unknown;
	output: unknown;
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

function stageArtifact(entries: StageTraceEntry[]) {
	return {
		stage_trace: entries.map((entry) => ({
			...entry,
			status: "completed",
		})),
	};
}

const DEMO_TOOL_STEP_BLUEPRINTS: DemoToolStepBlueprint[] = [
	{
		name: "list_skills",
		input: () => ({ path: "skills" }),
		output: {
			status: "ok",
			artifact: stageArtifact([
				{
					id: "skill-context",
					name: "skill_context",
					summary: "listed available workspace skill descriptions",
				},
			]),
			skills: ["billing-tabular-pipeline", "gcp-cost-pipeline"],
		},
	},
	{
		name: "prep_stage",
		input: (message) => ({
			message,
			source_files: DEMO_SOURCE_FILES,
		}),
		output: {
			status: "ok",
			artifact: stageArtifact([
				{
					id: "prep-profile",
					name: "prep",
					summary: "profiled source files and selected tabular targets",
				},
			]),
			prepared_state: DEMO_PREPARED_STATE,
		},
	},
	{
		name: "query_stage",
		input: (message) => ({
			message,
			prepared_state: DEMO_PREPARED_STATE,
		}),
		output: {
			status: "ok",
			content: "Returned 2 row(s) across 2 column(s) from the demo query.",
			artifact: stageArtifact([
				{
					id: "sql-write",
					name: "sql",
					summary: "wrote SQL file data/sql/demo-trace.sql",
				},
				{
					id: "sql-execute",
					name: "sql",
					summary: "execute succeeded on attempt 1",
				},
			]),
			result: {
				columns: ["id", "amount"],
				rows: [
					{ amount: 10, id: 1 },
					{ amount: 20, id: 2 },
				],
			},
		},
	},
	{
		name: "validation_stage",
		input: () => ({
			result_summary: "Returned 2 row(s) across 2 column(s).",
		}),
		output: {
			status: "ok",
			artifact: stageArtifact([
				{
					id: "validation",
					name: "validation",
					summary: "accepted the SQL result",
				},
			]),
			valid: true,
		},
	},
	{
		name: "save_view",
		input: () => ({
			sql_path: "data/sql/demo-trace.sql",
			view_name: "analysis_result_demo_trace",
		}),
		output: {
			status: "ok",
			artifact: stageArtifact([
				{
					id: "save",
					name: "save",
					summary: "saved result as view analysis_result_demo_trace",
				},
			]),
			view_name: "analysis_result_demo_trace",
		},
	},
];

function demoToolSteps(message: string): DemoToolStep[] {
	return DEMO_TOOL_STEP_BLUEPRINTS.map((step) => ({
		name: step.name,
		input: step.input(message),
		output: step.output,
	}));
}

export function buildDemoTraceChunks(message: string): UIMessageChunk[] {
	const messageId = crypto.randomUUID();
	const textId = crypto.randomUUID();

	return [
		{ type: "start", messageId },
		{ type: "text-start", id: textId },
		{
			type: "text-delta",
			id: textId,
			delta: "I will run the staged data workflow and show each tool step.",
		},
		...demoToolSteps(message).flatMap((step) => {
			const toolCallId = crypto.randomUUID();
			return [
				{
					type: "tool-input-available",
					toolCallId,
					toolName: step.name,
					input: step.input,
				} satisfies UIMessageChunk,
				{
					type: "tool-output-available",
					toolCallId,
					output: step.output,
				} satisfies UIMessageChunk,
			];
		}),
		{
			type: "text-delta",
			id: textId,
			delta:
				"\n\nDemo run complete. The trace exercised skills, prep, SQL, validation, and save without calling a model.",
		},
		{ type: "text-end", id: textId },
		{ type: "finish", finishReason: "stop" },
	];
}
