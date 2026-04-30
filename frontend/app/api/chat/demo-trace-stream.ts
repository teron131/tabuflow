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
const DEMO_SKILL_FILES = [
	"skills/gcp-cost-pipeline/SKILL.md",
	"skills/gcp-cost-pipeline/references/gcp-cost-view-contract.md",
	"skills/gcp-cost-pipeline/references/gcp-cost-view-stack.sql",
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
		input: () => ({ names: ["billing-tabular-pipeline", "gcp-cost-pipeline"] }),
		summary: "loaded billing-tabular-pipeline and gcp-cost-pipeline",
		output: { skills: ["billing-tabular-pipeline", "gcp-cost-pipeline"] },
	},
	{
		name: "fs_list_files",
		input: () => ({
			path: "skills/gcp-cost-pipeline",
			glob_pattern: "**/*",
			max_files: 200,
		}),
		summary: JSON.stringify(DEMO_SKILL_FILES),
		output: { files: DEMO_SKILL_FILES },
	},
	{
		name: "fs_read_text",
		input: () => ({ path: "skills/gcp-cost-pipeline/SKILL.md" }),
		summary: "read GCP cost skill instructions and routing metadata",
		output: {
			content:
				"---\nname: gcp-cost-pipeline\ndescription: Use when asked to build or analyze GCP billing account, category, or summary payloads...",
		},
	},
	{
		name: "fs_read_hashline",
		input: () => ({
			path: "skills/gcp-cost-pipeline/references/gcp-cost-view-stack.sql",
			start_line: 1,
			end_line: 24,
		}),
		summary: "1#6f8a07:-- GCP cost view stack reference.",
		output: {
			lines: [
				"1#6f8a07:-- GCP cost view stack reference.",
				"2#8de589:-- Replace placeholder table and column names with names discovered from sql_describe.",
			],
		},
	},
	{
		name: "fs_edit_hashline",
		input: () => ({
			path: "skills/gcp-cost-pipeline/SKILL.md",
			replacements: [
				{
					line_ref: "58#demo",
					text: "Recover currency_exchange_rate from invoice pre-header rows.",
				},
			],
		}),
		summary: "updated FX and payload-contract guidance in SKILL.md",
		output: { changed_lines: 1 },
	},
	{
		name: "sql_describe",
		input: () => ({ target: DEMO_PREPARED_STATE.preferred_targets[0] }),
		summary: "content_23ac1d333f4101ab_typed has 19057 rows x 18 columns",
		output: {
			prepared_state: DEMO_PREPARED_STATE,
			columns: [
				"billing_account_name",
				"service_description",
				"cost",
				"credits",
				"currency",
			],
		},
	},
	{
		name: "sql_execute",
		input: (message) => ({
			message,
			sql: "SELECT billing_account_name, SUM(cost) AS cost_usd FROM content_23ac1d333f4101ab_typed GROUP BY 1;",
		}),
		summary: "returned 3 account payload rows with USD and HKD totals",
		output: {
			result: {
				columns: ["billing_account_name", "cost_usd", "customer_charge_hkd"],
				rows: [
					{
						billing_account_name: "GCP Resale A",
						cost_usd: 1210.42,
						customer_charge_hkd: 9501.8,
					},
					{
						billing_account_name: "GCP Resale B",
						cost_usd: 884.17,
						customer_charge_hkd: 6940.73,
					},
					{
						billing_account_name: "Shared Services",
						cost_usd: 329.56,
						customer_charge_hkd: 2587.05,
					},
				],
			},
		},
	},
	{
		name: "sql_save_view",
		input: () => ({
			view_name: "gcp_current_summary_payload",
			sql_path: "data/sql/gcp-current-summary-payload.sql",
		}),
		summary: "saved view gcp_current_summary_payload",
		output: { view_name: "gcp_current_summary_payload" },
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
				"I will run the staged GCP cost workflow and show each backend tool step.",
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
				"\n\nDemo run complete. The trace exercised skill loading, file tools, hashline editing, SQL execution, and view saving without calling a model.",
		},
		{ type: "text-end", id: textId },
		{ type: "finish", finishReason: "stop" },
	];
}
