import type { CSSProperties } from "react";

export type ExplorerKey = "files" | "sql" | "views" | "skills";
export type ExplorerRailMode = ExplorerKey | "all";
export type SidePanel = "explorer" | "settings";
export type ThemeMode = "light" | "dark";
export type InspectorView =
	| "run"
	| "results"
	| "sqlArtifact"
	| "skill"
	| "skillResource"
	| "source";

export type RoundingSettings = {
	enabled: boolean;
	digits: number;
};

export type UploadedWorkspaceFile = {
	name: string;
	path: string;
	contentType?: string;
	artifactBackend?: string;
};

export type ShellStyle = CSSProperties & {
	"--explorer-width": string;
	"--chat-width": string;
	"--chat-dock-height": string;
	"--query-height": string;
	"--query-min-height": string;
	"--workbench-ui-scale"?: string;
};
