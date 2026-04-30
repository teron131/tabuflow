import { Files, Settings2 } from "lucide-react";
import { memo } from "react";
import type { ExplorerKey, SidePanel } from "./types";

type ActivityBarProps = {
	activeExplorer: ExplorerKey;
	isExplorerCollapsed: boolean;
	sidePanel: SidePanel;
	onToggleFiles: () => void;
	onOpenSettings: () => void;
};

export const ActivityBar = memo(function ActivityBar({
	activeExplorer,
	isExplorerCollapsed,
	sidePanel,
	onToggleFiles,
	onOpenSettings,
}: ActivityBarProps) {
	return (
		<nav className="activity-bar" aria-label="Side panel controls">
			<button
				className={
					sidePanel === "explorer" &&
					activeExplorer === "files" &&
					!isExplorerCollapsed
						? "activity-panel-toggle active"
						: "activity-panel-toggle"
				}
				onClick={onToggleFiles}
				type="button"
				aria-label={
					isExplorerCollapsed || sidePanel !== "explorer"
						? "Show files"
						: "Toggle files"
				}
				aria-expanded={!isExplorerCollapsed}
			>
				<Files size={17} />
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
