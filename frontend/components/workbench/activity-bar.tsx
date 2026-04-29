import { PanelLeft } from "lucide-react";
import { explorerItems } from "./constants";
import type { ExplorerKey } from "./types";

type ActivityBarProps = {
	activeExplorer: ExplorerKey;
	isExplorerCollapsed: boolean;
	onToggleExplorer: () => void;
	onSelectExplorer: (key: ExplorerKey) => void;
};

export function ActivityBar({
	activeExplorer,
	isExplorerCollapsed,
	onToggleExplorer,
	onSelectExplorer,
}: ActivityBarProps) {
	return (
		<nav className="activity-bar" aria-label="Explorer modes">
			<button
				className="activity-panel-toggle"
				onClick={onToggleExplorer}
				type="button"
				aria-label={isExplorerCollapsed ? "Expand explorer" : "Collapse explorer"}
				aria-expanded={!isExplorerCollapsed}
			>
				<PanelLeft size={17} />
			</button>
			{explorerItems.map((item) => {
				const Icon = item.icon;
				return (
					<button
						key={item.key}
						className={activeExplorer === item.key ? "active" : ""}
						onClick={() => onSelectExplorer(item.key)}
						type="button"
						aria-label={item.label}
					>
						<Icon size={17} />
					</button>
				);
			})}
		</nav>
	);
}
