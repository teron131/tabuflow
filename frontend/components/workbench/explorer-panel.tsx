import { ChevronRight, PanelLeft } from "lucide-react";
import type { BootstrapPayload, SkillEntry, SourceFile, Target } from "@/lib/api";
import { badgeTone, fileBadge, targetBadge } from "./badges";
import type { ExplorerKey } from "./types";

type ExplorerPanelProps = {
	activeExplorer: ExplorerKey;
	bootstrap: BootstrapPayload;
	isCollapsed: boolean;
	selectedSkill: SkillEntry | null;
	selectedSource: SourceFile | null;
	selectedTarget: Target | null;
	skills: SkillEntry[];
	onSelectSkill: (skill: SkillEntry) => void;
	onSelectSource: (source: SourceFile) => void;
	onSelectTarget: (target: Target) => void;
	onToggle: () => void;
};

function TreeBadge({ label }: { label: string }) {
	return <span className={`file-badge ${badgeTone(label)}`}>{label}</span>;
}

export function ExplorerPanel({
	activeExplorer,
	bootstrap,
	isCollapsed,
	selectedSkill,
	selectedSource,
	selectedTarget,
	skills,
	onSelectSkill,
	onSelectSource,
	onSelectTarget,
	onToggle,
}: ExplorerPanelProps) {
	const viewItems = bootstrap.targets.filter((target) => target.kind === "view");

	return (
		<aside className={isCollapsed ? "explorer collapsed" : "explorer"}>
			<header className="panel-title">
				<span>EXPLORER</span>
				<button
					className="panel-toggle"
					type="button"
					aria-label={isCollapsed ? "Expand explorer" : "Collapse explorer"}
					aria-expanded={!isCollapsed}
					onClick={onToggle}
				>
					<PanelLeft size={14} />
				</button>
			</header>
			<div className="explorer-list">
				{activeExplorer === "files" &&
					bootstrap.source_files.map((source) => {
						const badge = fileBadge(source.name, source.kind);
						const isActive = selectedSource?.id === source.id;
						return (
							<button
								key={source.id}
								className={isActive ? "tree-row active" : "tree-row"}
								onClick={() => onSelectSource(source)}
								type="button"
							>
								<ChevronRight size={12} />
								<TreeBadge label={badge} />
								<span>{source.name}</span>
								<small>{source.status}</small>
							</button>
						);
					})}
				{activeExplorer === "sql" &&
					bootstrap.targets.map((target) => {
						const badge = targetBadge(target.kind);
						const isActive = selectedTarget?.name === target.name;
						return (
							<button
								key={target.name}
								className={isActive ? "tree-row active" : "tree-row"}
								onClick={() => onSelectTarget(target)}
								type="button"
							>
								<ChevronRight size={12} />
								<TreeBadge label={badge} />
								<span>{target.name}</span>
								<small>{target.row_count ?? "-"}</small>
							</button>
						);
					})}
				{activeExplorer === "views" &&
					viewItems.map((target) => {
						const badge = targetBadge(target.kind);
						const isActive = selectedTarget?.name === target.name;
						return (
							<button
								key={target.name}
								className={isActive ? "tree-row active" : "tree-row"}
								onClick={() => onSelectTarget(target)}
								type="button"
							>
								<ChevronRight size={12} />
								<TreeBadge label={badge} />
								<span>{target.name}</span>
								<small>{target.source_path_count}</small>
							</button>
						);
					})}
				{activeExplorer === "skills" &&
					skills.map((skill) => (
						<button
							key={skill.name}
							className={
								selectedSkill?.name === skill.name
									? "tree-row active"
									: "tree-row"
							}
							onClick={() => onSelectSkill(skill)}
							type="button"
						>
							<ChevronRight size={12} />
							<TreeBadge label="SKILL" />
							<span>{skill.name}</span>
							<small>{skill.references?.length ?? 0}</small>
						</button>
					))}
			</div>
		</aside>
	);
}
