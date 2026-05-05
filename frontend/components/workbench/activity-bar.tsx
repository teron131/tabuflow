import {
	BookOpenCheck,
	BrainCircuit,
	ChevronsDown,
	Files,
	LayoutGrid,
	type LucideIcon,
	Moon,
	Settings2,
	Sun,
} from "lucide-react";
import { memo } from "react";
import type {
	ExplorerKey,
	ExplorerRailMode,
	SidePanel,
	ThemeMode,
} from "./types";

type ActivityBarProps = {
	activeMode: ExplorerRailMode;
	isExplorerCollapsed: boolean;
	sidePanel: SidePanel;
	themeMode: ThemeMode;
	onExpandExplorer: () => void;
	onOpenSettings: () => void;
	onSelectExplorer: (section: ExplorerKey) => void;
	onToggleTheme: () => void;
};

type ExplorerAction = {
	key: ExplorerKey;
	label: string;
	icon: LucideIcon;
};

const explorerActions: ExplorerAction[] = [
	{ key: "files", label: "sources", icon: Files },
	{ key: "sql", label: "tables", icon: LayoutGrid },
	{ key: "views", label: "views", icon: BookOpenCheck },
	{ key: "skills", label: "skills", icon: BrainCircuit },
];

export const ActivityBar = memo(function ActivityBar({
	activeMode,
	isExplorerCollapsed,
	sidePanel,
	themeMode,
	onExpandExplorer,
	onOpenSettings,
	onSelectExplorer,
	onToggleTheme,
}: ActivityBarProps) {
	const ThemeIcon = themeMode === "dark" ? Sun : Moon;
	const nextThemeLabel =
		themeMode === "dark" ? "Switch to light mode" : "Switch to dark mode";

	return (
		<nav className="activity-bar" aria-label="Side panel controls">
			<button
				aria-label="Expand all explorer sections"
				aria-expanded={
					sidePanel === "explorer" &&
					activeMode === "all" &&
					!isExplorerCollapsed
				}
				className={
					sidePanel === "explorer" &&
					activeMode === "all" &&
					!isExplorerCollapsed
						? "activity-panel-toggle active"
						: "activity-panel-toggle"
				}
				onClick={onExpandExplorer}
				title="Expand all explorer sections"
				type="button"
			>
				<ChevronsDown size={17} />
			</button>
			{explorerActions.map((action) => {
				const Icon = action.icon;
				const isActive =
					sidePanel === "explorer" &&
					activeMode === action.key &&
					!isExplorerCollapsed;
				return (
					<button
						aria-expanded={isActive}
						aria-label={`Show ${action.label}`}
						className={
							isActive
								? "activity-panel-toggle active"
								: "activity-panel-toggle"
						}
						key={action.key}
						onClick={() => onSelectExplorer(action.key)}
						title={`Show ${action.label}`}
						type="button"
					>
						<Icon size={17} />
					</button>
				);
			})}
			<button
				aria-label={nextThemeLabel}
				aria-pressed={themeMode === "dark"}
				className="activity-theme-button"
				onClick={onToggleTheme}
				title={nextThemeLabel}
				type="button"
			>
				<ThemeIcon size={17} />
			</button>
			<button
				className={
					sidePanel === "settings" && !isExplorerCollapsed
						? "activity-settings-button active"
						: "activity-settings-button"
				}
				onClick={onOpenSettings}
				type="button"
				aria-label="Open frontend settings"
			>
				<Settings2 size={17} />
			</button>
		</nav>
	);
});
