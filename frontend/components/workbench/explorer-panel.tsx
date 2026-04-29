import {
	ArrowDownAZ,
	ArrowUpAZ,
	ChevronDown,
	ChevronRight,
	Folder,
	FolderOpen,
	ListFilter,
	PanelLeft,
	Search,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type {
	BootstrapPayload,
	SkillEntry,
	SourceFile,
	Target,
} from "@/lib/api";
import { fileBadge, targetBadge } from "./badges";
import { isTargetView } from "./targets";
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

type SortKey = "name" | "type" | "status";
type SortDirection = "asc" | "desc";

type ExplorerRow = {
	id: string;
	label: string;
	type: string;
	status: string;
	detail: string;
	iconType: string;
	isActive: boolean;
	onSelect: () => void;
};

type ExplorerGroup = {
	key: ExplorerKey;
	label: string;
	rows: ExplorerRow[];
};

const groupLabels: Record<ExplorerKey, string> = {
	files: "Sources",
	sql: "Queryable targets",
	views: "Saved views",
	skills: "Skills",
};

const defaultOpenGroups: Record<ExplorerKey, boolean> = {
	files: true,
	sql: true,
	views: true,
	skills: true,
};

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
	const [query, setQuery] = useState("");
	const [typeFilter, setTypeFilter] = useState("all");
	const [sortKey, setSortKey] = useState<SortKey>("name");
	const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
	const [openGroups, setOpenGroups] = useState(defaultOpenGroups);

	useEffect(() => {
		setOpenGroups((groups) => ({ ...groups, [activeExplorer]: true }));
	}, [activeExplorer]);

	const groups = useMemo(
		() =>
			buildGroups({
				bootstrap,
				selectedSkill,
				selectedSource,
				selectedTarget,
				skills,
				onSelectSkill,
				onSelectSource,
				onSelectTarget,
			}),
		[
			bootstrap,
			selectedSkill,
			selectedSource,
			selectedTarget,
			skills,
			onSelectSkill,
			onSelectSource,
			onSelectTarget,
		],
	);

	const typeOptions = useMemo(() => {
		const values = new Set<string>();
		for (const group of groups) {
			for (const row of group.rows) {
				values.add(row.type);
			}
		}
		return [
			"all",
			...Array.from(values).sort((left, right) => left.localeCompare(right)),
		];
	}, [groups]);

	const visibleGroups = groups.map((group) => ({
		...group,
		rows: sortRows(
			filterRows(group.rows, query, typeFilter),
			sortKey,
			sortDirection,
		),
	}));
	const hasVisibleRows = visibleGroups.some((group) => group.rows.length > 0);

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
			<div className="explorer-tools">
				<label className="explorer-search">
					<Search size={13} />
					<input
						aria-label="Search explorer"
						value={query}
						onChange={(event) => setQuery(event.target.value)}
						placeholder="Search"
					/>
				</label>
				<label className="explorer-select">
					<ListFilter size={13} />
					<select
						aria-label="Filter explorer by type"
						value={typeFilter}
						onChange={(event) => setTypeFilter(event.target.value)}
					>
						{typeOptions.map((option) => (
							<option key={option} value={option}>
								{option === "all" ? "All types" : option}
							</option>
						))}
					</select>
				</label>
				<label className="explorer-select">
					<span>AZ</span>
					<select
						aria-label="Sort explorer"
						value={sortKey}
						onChange={(event) => setSortKey(event.target.value as SortKey)}
					>
						<option value="name">Name</option>
						<option value="type">Type</option>
						<option value="status">Status</option>
					</select>
				</label>
				<button
					className="sort-direction-button"
					type="button"
					aria-label={`Sort ${sortDirection === "asc" ? "ascending" : "descending"}`}
					onClick={() =>
						setSortDirection((current) => (current === "asc" ? "desc" : "asc"))
					}
				>
					{sortDirection === "asc" ? (
						<ArrowDownAZ size={14} />
					) : (
						<ArrowUpAZ size={14} />
					)}
				</button>
			</div>
			<div className="explorer-list">
				{visibleGroups.map((group) => {
					const isOpen = openGroups[group.key];
					const isActiveGroup = activeExplorer === group.key;
					const FolderIcon = isOpen ? FolderOpen : Folder;
					return (
						<section className="tree-group" key={group.key}>
							<button
								className={isActiveGroup ? "tree-folder active" : "tree-folder"}
								type="button"
								onClick={() =>
									setOpenGroups((groups) => ({
										...groups,
										[group.key]: !groups[group.key],
									}))
								}
							>
								{isOpen ? (
									<ChevronDown size={13} />
								) : (
									<ChevronRight size={13} />
								)}
								<FolderIcon size={15} />
								<span>{group.label}</span>
								<small>{group.rows.length}</small>
							</button>
							{isOpen &&
								group.rows.map((row) => (
									<button
										key={row.id}
										className={row.isActive ? "tree-row active" : "tree-row"}
										onClick={row.onSelect}
										title={row.detail || row.label}
										type="button"
									>
										<span className="tree-spacer" />
										<FileTypeIcon type={row.iconType} />
										<span className="tree-label">{row.label}</span>
										<small>{row.status}</small>
									</button>
								))}
						</section>
					);
				})}
				{!hasVisibleRows && <div className="explorer-empty">No matches</div>}
			</div>
		</aside>
	);
}

function buildGroups({
	bootstrap,
	selectedSkill,
	selectedSource,
	selectedTarget,
	skills,
	onSelectSkill,
	onSelectSource,
	onSelectTarget,
}: {
	bootstrap: BootstrapPayload;
	selectedSkill: SkillEntry | null;
	selectedSource: SourceFile | null;
	selectedTarget: Target | null;
	skills: SkillEntry[];
	onSelectSkill: (skill: SkillEntry) => void;
	onSelectSource: (source: SourceFile) => void;
	onSelectTarget: (target: Target) => void;
}): ExplorerGroup[] {
	const viewItems = bootstrap.targets.filter(isTargetView);
	const targetItems = bootstrap.targets.filter(
		(target) => !isTargetView(target),
	);

	return [
		{
			key: "files",
			label: groupLabels.files,
			rows: bootstrap.source_files.map((source) => ({
				id: `source-${source.id}`,
				label: source.name,
				type: fileBadge(source.name, source.kind),
				status: source.status,
				detail: source.source_path || source.destination_path || "",
				iconType: sourceIconType(source),
				isActive: selectedSource?.id === source.id,
				onSelect: () => onSelectSource(source),
			})),
		},
		{
			key: "sql",
			label: groupLabels.sql,
			rows: targetItems.map((target) => ({
				id: `target-${target.name}`,
				label: target.name,
				type: targetBadge(target.kind),
				status: target.row_count == null ? "-" : String(target.row_count),
				detail: target.summary,
				iconType: targetIconType(target),
				isActive: selectedTarget?.name === target.name,
				onSelect: () => onSelectTarget(target),
			})),
		},
		{
			key: "views",
			label: groupLabels.views,
			rows: viewItems.map((target) => ({
				id: `view-${target.name}`,
				label: target.name,
				type: targetBadge(target.kind),
				status: String(target.source_path_count),
				detail: target.summary,
				iconType: "view",
				isActive: selectedTarget?.name === target.name,
				onSelect: () => onSelectTarget(target),
			})),
		},
		{
			key: "skills",
			label: groupLabels.skills,
			rows: skills.map((skill) => ({
				id: `skill-${skill.name}`,
				label: skill.name,
				type: "SKILL",
				status: String(skill.references?.length ?? 0),
				detail: skill.description || skill.path || "",
				iconType: "skill",
				isActive: selectedSkill?.name === skill.name,
				onSelect: () => onSelectSkill(skill),
			})),
		},
	];
}

function filterRows(rows: ExplorerRow[], query: string, typeFilter: string) {
	const normalizedQuery = query.trim().toLowerCase();
	return rows.filter((row) => {
		if (typeFilter !== "all" && row.type !== typeFilter) {
			return false;
		}
		if (!normalizedQuery) {
			return true;
		}
		return [row.label, row.type, row.status, row.detail]
			.join(" ")
			.toLowerCase()
			.includes(normalizedQuery);
	});
}

function sortRows(
	rows: ExplorerRow[],
	sortKey: SortKey,
	direction: SortDirection,
) {
	const multiplier = direction === "asc" ? 1 : -1;
	return [...rows].sort((left, right) => {
		const leftValue = sortValue(left, sortKey);
		const rightValue = sortValue(right, sortKey);
		return (
			leftValue.localeCompare(rightValue, undefined, { numeric: true }) *
			multiplier
		);
	});
}

function sortValue(row: ExplorerRow, sortKey: SortKey) {
	if (sortKey === "type") {
		return row.type;
	}
	if (sortKey === "status") {
		return row.status;
	}
	return row.label;
}

function sourceIconType(source: SourceFile) {
	const kind = source.kind.toLowerCase();
	if (kind.includes("csv")) {
		return "csv";
	}
	if (kind.includes("sqlite") || kind.includes("db")) {
		return "db";
	}
	return "file";
}

function targetIconType(target: Target) {
	if (target.kind === "raw_content_table") {
		return "raw";
	}
	if (target.kind === "typed_content_view") {
		return "typed";
	}
	return isTargetView(target) ? "view" : "db";
}

function FileTypeIcon({ type }: { type: string }) {
	const iconType = ["csv", "db", "raw", "view", "typed", "skill"].includes(type)
		? type
		: "file";
	return (
		<svg
			className={`file-type-icon ${iconType}`}
			viewBox="0 0 32 32"
			aria-hidden="true"
		>
			{iconType === "csv" && (
				<>
					<rect x="5" y="6" width="22" height="20" rx="2" />
					<path d="M5 12h22M5 19h22M12 6v20M20 6v20" />
				</>
			)}
			{(iconType === "db" || iconType === "raw") && (
				<>
					<ellipse cx="16" cy="8" rx="10" ry="4" />
					<path d="M6 8v15c0 2.2 4.5 4 10 4s10-1.8 10-4V8" />
					<path d="M6 15c0 2.2 4.5 4 10 4s10-1.8 10-4" />
					<path d="M6 22c0 2.2 4.5 4 10 4s10-1.8 10-4" />
					{iconType === "raw" && <path d="M12 12h8M11 18h10" />}
				</>
			)}
			{iconType === "view" && (
				<>
					<rect x="5" y="7" width="22" height="18" rx="2" />
					<path d="M9 13h14M9 19h14M13 9v16" />
					<path d="M20 6l6 6" />
				</>
			)}
			{iconType === "typed" && (
				<>
					<rect x="5" y="7" width="22" height="18" rx="2" />
					<path d="M9 13h14M9 19h9M13 9v16" />
					<path d="M20 21l2.2 2.2L27 17" />
				</>
			)}
			{iconType === "skill" && (
				<>
					<rect x="6" y="5" width="20" height="22" rx="2" />
					<path d="M11 12h10M11 17h10M11 22h6" />
					<path d="M23 4v7h6" />
				</>
			)}
			{iconType === "file" && (
				<>
					<rect x="6" y="5" width="20" height="22" rx="2" />
					<path d="M20 5v7h6M11 15h10M11 20h8" />
				</>
			)}
		</svg>
	);
}
