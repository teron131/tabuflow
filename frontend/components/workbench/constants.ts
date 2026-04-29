import {
	Braces,
	Database,
	FileCode2,
	Files,
	ScrollText,
	Settings2,
	Table2,
} from "lucide-react";
import type { CenterTab, ExplorerKey, NavigationItem } from "./types";

export const modelOptions = [
	"gpt-5.4-nano",
	"gpt-5.4-mini",
	"gpt-5.4",
	"gpt-5.5",
];

export const explorerItems: Array<NavigationItem<ExplorerKey>> = [
	{ key: "files", label: "Files", icon: Files },
	{ key: "sql", label: "SQL", icon: FileCode2 },
	{ key: "views", label: "Views", icon: Table2 },
	{ key: "skills", label: "Skills", icon: Settings2 },
];

export const workspaceTabs: Array<NavigationItem<CenterTab>> = [
	{ key: "sql", label: "SQL", icon: FileCode2 },
	{ key: "results", label: "Results", icon: Table2 },
	{ key: "target", label: "Target", icon: Database },
	{ key: "skill", label: "Skill", icon: ScrollText },
	{ key: "source", label: "Source", icon: Braces },
];

export const sqlKeywords = new Set([
	"SELECT",
	"FROM",
	"WHERE",
	"GROUP",
	"BY",
	"ORDER",
	"LIMIT",
	"JOIN",
	"AS",
	"AND",
	"OR",
	"COUNT",
	"SUM",
	"AVG",
	"MIN",
	"MAX",
	"DISTINCT",
	"DESC",
	"ASC",
]);
