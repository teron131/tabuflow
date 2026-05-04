import { Icon as IconifyIcon } from "@iconify/react";
import {
	ArrowDownAZ,
	ArrowUpAZ,
	BookOpenCheck,
	Braces,
	BrainCircuit,
	ChevronDown,
	ChevronRight,
	Files,
	FileText,
	Folder,
	FolderOpen,
	LayoutGrid,
	ListFilter,
	type LucideIcon,
	PanelLeft,
	Search,
	Table2,
} from "lucide-react";
import { type CSSProperties, memo, useEffect, useMemo, useState } from "react";
import {
	type BootstrapPayload,
	type SkillEntry,
	type SkillResourceEntry,
	type SkillResourcePayload,
	type SourceFile,
	skillLineCount,
	type Target,
} from "@/lib/api";
import { fileBadge, targetBadge } from "./badges";
import { isTargetView } from "./targets";
import type { ExplorerKey } from "./types";

type ExplorerPanelProps = {
	activeExplorer: ExplorerKey;
	bootstrap: BootstrapPayload;
	isCollapsed: boolean;
	selectedSkill: SkillEntry | null;
	selectedSkillResource: SkillResourceEntry | null;
	selectedSource: SourceFile | null;
	selectedTarget: Target | null;
	skills: SkillEntry[];
	onSelectSkill: (skill: SkillEntry) => void;
	onSelectSkillResource: (resource: SkillResourceEntry) => void;
	onSelectSource: (source: SourceFile) => void;
	onSelectTarget: (target: Target) => void;
	onToggle: () => void;
};

type SortKey = "name" | "type" | "status";
type SortDirection = "asc" | "desc";
type IconType =
	| "csv"
	| "database"
	| "file"
	| "markdown"
	| "python"
	| "raw"
	| "exampleFolder"
	| "referenceFolder"
	| "scriptFolder"
	| "skillFolder"
	| "sqlite"
	| "view";

type ExplorerRow = {
	id: string;
	label: string;
	type: string;
	status: string;
	detail: string;
	metadata: string;
	iconType: IconType;
	isActive: boolean;
	onSelect: () => void;
	children?: ExplorerRow[];
};

type FileTypeIconValue = LucideIcon | string;

type ExplorerGroup = {
	key: ExplorerKey;
	label: string;
	rows: ExplorerRow[];
};

const groupLabels: Record<ExplorerKey, string> = {
	files: "Sources",
	sql: "Extracted tables",
	views: "Queried views",
	skills: "Skills",
};

const defaultOpenGroups: Record<ExplorerKey, boolean> = {
	files: true,
	sql: true,
	views: true,
	skills: true,
};

const groupIcons: Record<ExplorerKey, LucideIcon> = {
	files: Files,
	sql: LayoutGrid,
	views: BookOpenCheck,
	skills: BrainCircuit,
};

const fileTypeIcons: Record<IconType, FileTypeIconValue> = {
	csv: "material-icon-theme:table",
	database: "material-icon-theme:database",
	exampleFolder: FolderOpen,
	file: FileText,
	markdown: "material-icon-theme:markdown",
	python: "material-icon-theme:python",
	raw: Table2,
	referenceFolder: FolderOpen,
	scriptFolder: FolderOpen,
	skillFolder: FolderOpen,
	sqlite: "material-icon-theme:database",
	view: Braces,
};

export const ExplorerPanel = memo(function ExplorerPanel({
	activeExplorer,
	bootstrap,
	isCollapsed,
	selectedSkill,
	selectedSkillResource,
	selectedSource,
	selectedTarget,
	skills,
	onSelectSkill,
	onSelectSkillResource,
	onSelectSource,
	onSelectTarget,
	onToggle,
}: ExplorerPanelProps) {
	const [query, setQuery] = useState("");
	const [typeFilter, setTypeFilter] = useState("all");
	const [sortKey, setSortKey] = useState<SortKey>("name");
	const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
	const [openGroups, setOpenGroups] = useState(defaultOpenGroups);
	const [openSkillRows, setOpenSkillRows] = useState<Record<string, boolean>>(
		{},
	);

	useEffect(() => {
		setOpenGroups((groups) => ({ ...groups, [activeExplorer]: true }));
	}, [activeExplorer]);

	const groups = useMemo(
		() =>
			buildGroups({
				bootstrap,
				selectedSkill,
				selectedSkillResource,
				selectedSource,
				selectedTarget,
				skills,
				onSelectSkill,
				onSelectSkillResource,
				onSelectSource,
				onSelectTarget,
			}),
		[
			bootstrap,
			selectedSkill,
			selectedSkillResource,
			selectedSource,
			selectedTarget,
			skills,
			onSelectSkill,
			onSelectSkillResource,
			onSelectSource,
			onSelectTarget,
		],
	);

	const typeOptions = useMemo(() => {
		const values = new Set<string>();
		for (const group of groups) {
			collectRowTypes(group.rows, values);
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
	const isFiltering = query.trim().length > 0 || typeFilter !== "all";
	const displayedGroups = isFiltering
		? visibleGroups.filter((group) => group.rows.length > 0)
		: visibleGroups;
	const hasVisibleRows = visibleGroups.some((group) => group.rows.length > 0);

	const renderTreeRow = (row: ExplorerRow, depth = 0) => {
		const hasChildren = Boolean(row.children?.length);
		const rowOpen = openSkillRows[row.id] ?? true;
		return (
			<div
				className="tree-branch"
				key={row.id}
				style={
					{
						"--tree-indent": `${5 + depth * 10}px`,
					} as CSSProperties
				}
			>
				<button
					className={treeRowClass(row, depth > 0 ? "child" : undefined)}
					onClick={() => {
						if (row.type === "SKILL" || !hasChildren) {
							row.onSelect();
						}
						if (hasChildren) {
							setOpenSkillRows((rows) => ({
								...rows,
								[row.id]: !rowOpen,
							}));
						}
					}}
					title={row.detail || row.label}
					type="button"
					aria-expanded={hasChildren ? rowOpen : undefined}
				>
					{hasChildren ? (
						rowOpen ? (
							<ChevronDown className="tree-nest-toggle" size={13} />
						) : (
							<ChevronRight className="tree-nest-toggle" size={13} />
						)
					) : (
						<span className="tree-spacer" />
					)}
					<FileTypeIcon type={row.iconType} />
					<span className="tree-label">{row.label}</span>
					{row.status ? <small>{row.status}</small> : null}
				</button>
				{hasChildren && rowOpen
					? row.children?.map((child) => renderTreeRow(child, depth + 1))
					: null}
			</div>
		);
	};

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
						aria-label="Filter workbench sidebar"
						value={query}
						onChange={(event) => setQuery(event.target.value)}
						placeholder="Filter files, source, size"
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
				{displayedGroups.map((group) => {
					const isOpen = openGroups[group.key];
					const isActiveGroup = activeExplorer === group.key;
					const GroupIcon =
						groupIcons[group.key] ?? (isOpen ? FolderOpen : Folder);
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
								<GroupIcon size={15} />
								<span>{group.label}</span>
							</button>
							{isOpen &&
								group.rows.map((row) =>
									renderTreeRow(row, group.key === "skills" ? 1 : 0),
								)}
						</section>
					);
				})}
				{!hasVisibleRows && <div className="explorer-empty">No matches</div>}
			</div>
		</aside>
	);
});

function collectRowTypes(rows: ExplorerRow[], values: Set<string>) {
	for (const row of rows) {
		values.add(row.type);
		collectRowTypes(row.children || [], values);
	}
}

function buildGroups({
	bootstrap,
	selectedSkill,
	selectedSkillResource,
	selectedSource,
	selectedTarget,
	skills,
	onSelectSkill,
	onSelectSkillResource,
	onSelectSource,
	onSelectTarget,
}: {
	bootstrap: BootstrapPayload;
	selectedSkill: SkillEntry | null;
	selectedSkillResource: SkillResourceEntry | null;
	selectedSource: SourceFile | null;
	selectedTarget: Target | null;
	skills: SkillEntry[];
	onSelectSkill: (skill: SkillEntry) => void;
	onSelectSkillResource: (resource: SkillResourceEntry) => void;
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
				metadata: "",
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
				status: target.size_label || "",
				detail: target.summary,
				metadata: `${target.size_label || ""} ${target.row_count ?? ""} rows ${target.column_count ?? ""} columns ${target.source_path_count} sources`,
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
				status: target.size_label || "",
				detail: target.summary,
				metadata: `${target.size_label || ""} ${target.row_count ?? ""} rows ${target.column_count ?? ""} columns ${target.source_path_count} sources`,
				iconType: "view",
				isActive: selectedTarget?.name === target.name,
				onSelect: () => onSelectTarget(target),
			})),
		},
		{
			key: "skills",
			label: groupLabels.skills,
			rows: skills.map((skill) => {
				const lineCount = skillLineCount(skill);
				const children = skillChildRows(
					skill,
					selectedSkillResource,
					onSelectSkill,
					onSelectSkillResource,
				);
				return {
					id: `skill-${skill.name}`,
					label: skill.name,
					type: "SKILL",
					status: lineCount ? String(lineCount) : "",
					detail: skill.description || skill.path || "",
					metadata: [
						skill.path,
						skill.skills_path,
						lineCount,
						...children.map((child) => child.metadata),
					]
						.filter(Boolean)
						.join(" "),
					iconType: "skillFolder",
					isActive:
						selectedSkill?.name === skill.name && !selectedSkillResource,
					onSelect: () => onSelectSkill(skill),
					children,
				};
			}),
		},
	];
}

function treeRowClass(row: ExplorerRow, modifier?: string) {
	return [
		"tree-row",
		row.isActive ? "active" : "",
		modifier ? `tree-row-${modifier}` : "",
	]
		.filter(Boolean)
		.join(" ");
}

function filterRows(
	rows: ExplorerRow[],
	query: string,
	typeFilter: string,
): ExplorerRow[] {
	const normalizedQuery = query.trim().toLowerCase();
	return filterNestedRows(rows, normalizedQuery, typeFilter);
}

function filterNestedRows(
	rows: ExplorerRow[],
	normalizedQuery: string,
	typeFilter: string,
): ExplorerRow[] {
	const nextRows: ExplorerRow[] = [];
	for (const row of rows) {
		const children = filterNestedRows(
			row.children || [],
			normalizedQuery,
			typeFilter,
		);
		if (rowMatches(row, normalizedQuery, typeFilter) || children.length > 0) {
			nextRows.push({ ...row, children });
		}
	}
	return nextRows;
}

function rowMatches(
	row: ExplorerRow,
	normalizedQuery: string,
	typeFilter: string,
) {
	if (typeFilter !== "all" && row.type !== typeFilter) {
		return false;
	}
	if (!normalizedQuery) {
		return true;
	}
	return [row.label, row.type, row.status, row.detail, row.metadata]
		.join(" ")
		.toLowerCase()
		.includes(normalizedQuery);
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
		return "sqlite";
	}
	return "file";
}

function targetIconType(target: Target) {
	if (target.kind === "raw_content_table") {
		return "raw";
	}
	if (target.kind === "typed_content_view") {
		return "view";
	}
	return isTargetView(target) ? "view" : "raw";
}

function skillChildRows(
	skill: SkillEntry,
	selectedSkillResource: SkillResourceEntry | null,
	onSelectSkill: (skill: SkillEntry) => void,
	onSelectSkillResource: (resource: SkillResourceEntry) => void,
): ExplorerRow[] {
	const rows: ExplorerRow[] = [
		{
			id: `skill-doc-${skill.name}`,
			label: "SKILL.md",
			type: "MD",
			status: "",
			detail: skill.instructions?.relative_path || skill.path || "",
			metadata: skill.instructions?.content || skill.content || "",
			iconType: "markdown",
			isActive: false,
			onSelect: () => onSelectSkill(skill),
		},
	];

	const examples = skillResourceRows(
		skill,
		skill.examples || [],
		"EXAMPLE",
		"example",
		"examples",
		selectedSkillResource,
		onSelectSkillResource,
	);
	if (examples.length > 0) {
		rows.push(
			skillResourceFolderRow(
				skill,
				"examples",
				examples,
				"exampleFolder",
				onSelectSkill,
			),
		);
	}

	const references = skillResourceRows(
		skill,
		skill.references || [],
		"REF",
		"reference",
		"references",
		selectedSkillResource,
		onSelectSkillResource,
	);
	if (references.length > 0) {
		rows.push(
			skillResourceFolderRow(
				skill,
				"references",
				references,
				"referenceFolder",
				onSelectSkill,
			),
		);
	}

	const scripts = skillResourceRows(
		skill,
		skill.scripts || [],
		"SCRIPT",
		"script",
		"scripts",
		selectedSkillResource,
		onSelectSkillResource,
	);
	if (scripts.length > 0) {
		rows.push(
			skillResourceFolderRow(
				skill,
				"scripts",
				scripts,
				"scriptFolder",
				onSelectSkill,
			),
		);
	}

	return rows;
}

function skillResourceRows(
	skill: SkillEntry,
	resources: SkillResourcePayload[],
	type: "EXAMPLE" | "REF" | "SCRIPT",
	fallback: string,
	group: "examples" | "references" | "scripts",
	selectedSkillResource: SkillResourceEntry | null,
	onSelectSkillResource: (resource: SkillResourceEntry) => void,
): ExplorerRow[] {
	return resources.map((resource) => {
		const label = skillResourceFileName(resource.relative_path, fallback);
		const selection: SkillResourceEntry = {
			...resource,
			skillName: skill.name,
			label,
			group,
		};
		return {
			id: `skill-${type.toLowerCase()}-${skill.name}-${resource.relative_path || label}`,
			label,
			type,
			status: "",
			detail: resource.relative_path || "",
			metadata: resource.content || resource.relative_path || "",
			iconType: resourceIconType(label),
			isActive:
				selectedSkillResource?.skillName === selection.skillName &&
				selectedSkillResource?.group === selection.group &&
				selectedSkillResource?.relative_path === selection.relative_path,
			onSelect: () => onSelectSkillResource(selection),
		};
	});
}

function skillResourceFolderRow(
	skill: SkillEntry,
	label: "examples" | "references" | "scripts",
	children: ExplorerRow[],
	iconType: IconType,
	onSelectSkill: (skill: SkillEntry) => void,
): ExplorerRow {
	return {
		id: `skill-${label}-folder-${skill.name}`,
		label,
		type: "FOLDER",
		status: "",
		detail: `${skill.name} ${label}`,
		metadata: children.map((child) => child.metadata).join(" "),
		iconType,
		isActive: false,
		onSelect: () => onSelectSkill(skill),
		children,
	};
}

function skillResourceFileName(
	relativePath: string | undefined,
	fallback: string,
) {
	if (!relativePath) {
		return fallback;
	}
	return relativePath.split("/").filter(Boolean).at(-1) || fallback;
}

function resourceExtension(label: string) {
	const extension = label.split(".").at(-1);
	return extension && extension !== label ? extension : "";
}

function resourceIconType(label: string): IconType {
	const extension = resourceExtension(label).toLowerCase();
	if (extension === "csv") return "csv";
	if (extension === "md" || extension === "markdown") return "markdown";
	if (extension === "py") return "python";
	if (extension === "sql" || extension === "sqlite" || extension === "db") {
		return "database";
	}
	return "file";
}

function FileTypeIcon({ type }: { type: string }) {
	const iconType = type in fileTypeIcons ? (type as IconType) : "file";
	const Icon = fileTypeIcons[iconType];
	if (typeof Icon === "string") {
		return (
			<IconifyIcon
				className={`file-type-icon ${iconType}`}
				icon={Icon}
				aria-hidden="true"
			/>
		);
	}
	return (
		<Icon
			className={`file-type-icon ${iconType}`}
			size={18}
			strokeWidth={1.9}
			aria-hidden="true"
		/>
	);
}
