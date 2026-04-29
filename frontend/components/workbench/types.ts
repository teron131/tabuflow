import type { LucideIcon } from "lucide-react";
import type { CSSProperties } from "react";

export type ExplorerKey = "files" | "sql" | "views" | "skills";
export type SidePanel = "explorer" | "settings";
export type CenterTab = "sql" | "results" | "target" | "skill" | "source";

export type NavigationItem<Key extends string> = {
	key: Key;
	label: string;
	icon: LucideIcon;
};

export type ShellStyle = CSSProperties & {
	"--explorer-width": string;
	"--chat-width": string;
	"--query-height": string;
	"--query-min-height": string;
	"--workbench-ui-scale"?: string;
};
