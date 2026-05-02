import type { UIMessageChunk } from "ai";

const DEMO_TRACE_DELAYS = {
	fast: 50,
	slow: 1000,
} as const;
const DEFAULT_DEMO_TRACE_MESSAGE = "show all demo trace steps";
const DEMO_PREPARED_STATE = {
	database_path: "data/demo.sqlite",
	preferred_targets: ["table1"],
};
const DEMO_SKILL_FILES = [
	"skills/skill1/SKILL.md",
	"skills/skill1/references/file1.md",
	"skills/skill1/references/query1.sql",
];

type DemoToolStep = {
	name: string;
	input: unknown;
	summary: string;
	output?: Record<string, unknown>;
};
type DemoToolStepBlueprint = {
	name: string;
	input: (message: string) => unknown;
	summary: string;
	output?: Record<string, unknown>;
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
		input: () => ({ names: ["skill1", "skill2"] }),
		summary: "loaded skill1 and skill2",
		output: { skills: ["skill1", "skill2"] },
	},
	{
		name: "fs_list_files",
		input: () => ({
			path: "skills/skill1",
			glob_pattern: "**/*",
			max_files: 200,
		}),
		summary: JSON.stringify(DEMO_SKILL_FILES),
		output: { files: DEMO_SKILL_FILES },
	},
	{
		name: "fs_read_text",
		input: () => ({ path: "skills/skill1/SKILL.md" }),
		summary: "read skill1 instructions and routing metadata",
		output: {
			content:
				"---\nname: skill1\ndescription: Use when asked to build or analyze demo table payloads...",
		},
	},
	{
		name: "fs_read_hashline",
		input: () => ({
			path: "skills/skill1/references/query1.sql",
			start_line: 1,
			end_line: 24,
		}),
		summary: "1#6f8a07:-- Demo query reference.",
		output: {
			lines: [
				"1#6f8a07:-- Demo query reference.",
				"2#8de589:-- Replace table1 and column names with names discovered from sql_describe.",
			],
		},
	},
	{
		name: "fs_edit_hashline",
		input: () => ({
			path: "skills/skill1/SKILL.md",
			replacements: [
				{
					line_ref: "58#demo",
					text: "Keep file1 and table1 names aligned in the demo output.",
				},
			],
		}),
		summary: "updated demo output guidance in SKILL.md",
		output: { changed_lines: 1 },
	},
	{
		name: "sql_describe",
		input: () => ({ target: DEMO_PREPARED_STATE.preferred_targets[0] }),
		summary: "table1 has 100 rows x 4 columns",
		output: {
			prepared_state: DEMO_PREPARED_STATE,
			columns: ["column1", "column2", "column3", "column4"],
		},
	},
	{
		name: "sql_execute",
		input: (message) => ({
			message,
			sql: "SELECT column1, SUM(column2) AS total1 FROM table1 GROUP BY 1;",
		}),
		summary: "returned 3 demo rows from table1",
		output: {
			result: {
				columns: ["column1", "total1", "total2"],
				rows: [
					{
						column1: "row1",
						total1: 100,
						total2: 10,
					},
					{
						column1: "row2",
						total1: 200,
						total2: 20,
					},
					{
						column1: "row3",
						total1: 300,
						total2: 30,
					},
				],
			},
		},
	},
	{
		name: "sql_save_view",
		input: () => ({
			view_name: "view1",
			sql_path: "data/sql/query1.sql",
		}),
		summary: "saved view view1",
		output: { view_name: "view1" },
	},
];

function demoToolSteps(message: string): DemoToolStep[] {
	return DEMO_TOOL_STEP_BLUEPRINTS.map((step) => ({
		name: step.name,
		input: step.input(message),
		summary: step.summary,
		output: step.output,
	}));
}

function demoToolOutput(step: DemoToolStep, toolCallId: string) {
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
		},
		...step.output,
	};
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
			delta:
				"I will run the staged demo workflow and show each backend tool step.",
		},
		...demoToolSteps(message).flatMap((step) => {
			const toolCallId = crypto.randomUUID();
			return [
				{
					type: "tool-input-available",
					toolCallId,
					toolName: "backendChat",
					input: {
						tool: step.name,
						args: step.input,
					},
				} satisfies UIMessageChunk,
				{
					type: "tool-output-available",
					toolCallId,
					output: demoToolOutput(step, toolCallId),
				} satisfies UIMessageChunk,
			];
		}),
		{
			type: "text-delta",
			id: textId,
			delta:
				"\n\nDemo run complete. The trace exercised dummy skill loading, dummy file tools, hashline editing, SQL execution on table1, and view1 saving without calling a model.",
		},
		{ type: "text-end", id: textId },
		{ type: "finish", finishReason: "stop" },
	];
}
