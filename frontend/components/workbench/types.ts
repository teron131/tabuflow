import type { CSSProperties } from "react";

export type ExplorerKey = "files" | "sql" | "views" | "skills";
export type ExplorerRailMode = ExplorerKey | "all";
export type SidePanel = "explorer" | "settings";
export type InspectorView =
	| "run"
	| "results"
	| "target"
	| "skill"
	| "skillResource"
	| "source";

export type RoundingSettings = {
	enabled: boolean;
	digits: number;
};

export type ShellStyle = CSSProperties & {
	"--explorer-width": string;
	"--chat-width": string;
	"--chat-dock-height": string;
	"--query-height": string;
	"--query-min-height": string;
	"--workbench-ui-scale"?: string;
};
