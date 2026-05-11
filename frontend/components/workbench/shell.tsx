"use client";

import {
	type PointerEvent as ReactPointerEvent,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";
import { FaRobot } from "react-icons/fa";
import { ActivityBar } from "@/components/workbench/activity-bar";
import { AgentPanel } from "@/components/workbench/agent/panel";
import { BrandMark } from "@/components/workbench/brand-mark";
import {
	defaultRounding,
	modelOptions,
} from "@/components/workbench/constants";
import { ExplorerPanel } from "@/components/workbench/explorer/panel";
import { SettingsPanel } from "@/components/workbench/settings-panel";
import { isSqlArtifactView } from "@/components/workbench/sql-artifacts";
import type {
	ExplorerKey,
	ExplorerRailMode,
	InspectorView,
	RoundingSettings,
	SidePanel,
	ThemeMode,
	UploadedWorkspaceFile,
} from "@/components/workbench/types";
import {
	workbenchScale,
	workbenchScaleStyle,
} from "@/components/workbench/ui-scale";
import { usePaneLayout } from "@/components/workbench/use-pane-layout";
import { WorkspacePanel } from "@/components/workbench/workspace/panel";
import {
	type BootstrapPayload,
	emptyBootstrap,
	fetchJson,
	type HealthPayload,
	type SkillEntry,
	type SkillResourceEntry,
	type SkillResourcePayload,
	type SourceFile,
	type SqlArtifact,
	type SqlArtifactDetails,
	type SqlResult,
	saveSkillResource,
	skillContent,
	type UploadPayload,
	uploadWorkspaceFile,
} from "@/lib/api";

const themeStorageKey = "data-agentics-theme";
const previewRowLimit = 250;

function isThemeMode(value: string | null): value is ThemeMode {
	return value === "light" || value === "dark";
}

function preferredThemeMode(): ThemeMode {
	const savedTheme = window.localStorage.getItem(themeStorageKey);
	if (isThemeMode(savedTheme)) return savedTheme;
	return window.matchMedia("(prefers-color-scheme: dark)").matches
		? "dark"
		: "light";
}

export function Workbench() {
	const shellRef = useRef<HTMLElement | null>(null);
	const centerRef = useRef<HTMLElement | null>(null);
	const sqlArtifactPreviewRequestId = useRef(0);
	const sourcePreviewRequestId = useRef(0);
	const didSkipInitialThemePersist = useRef(false);
	const [activeExplorer, setActiveExplorer] = useState<ExplorerKey>("files");
	const [explorerRailMode, setExplorerRailMode] =
		useState<ExplorerRailMode>("all");
	const [sidePanel, setSidePanel] = useState<SidePanel>("explorer");
	const [isAgentPanelCollapsed, setIsAgentPanelCollapsed] = useState(false);
	const [explorerJumpToken, setExplorerJumpToken] = useState(0);
	const [inspectorView, setInspectorView] = useState<InspectorView>("results");
	const [rounding, setRounding] = useState<RoundingSettings>(defaultRounding);
	const [uiScale, setUiScale] = useState(workbenchScale.default);
	const [themeMode, setThemeMode] = useState<ThemeMode>("light");
	const [selectedModel, setSelectedModel] = useState("gpt-5.4-nano");
	const [bootstrap, setBootstrap] = useState<BootstrapPayload>(emptyBootstrap);
	const [sql, setSql] = useState(emptyBootstrap.sample_sql);
	const [sqlResult, setSqlResult] = useState<SqlResult | null>(null);
	const [sourcePreviewResult, setSourcePreviewResult] =
		useState<SqlResult | null>(null);
	const [sqlArtifactPreviewResult, setSqlArtifactPreviewResult] =
		useState<SqlResult | null>(null);
	const [selectedSqlArtifact, setSelectedSqlArtifact] =
		useState<SqlArtifact | null>(null);
	const [sqlArtifactSourceName, setSqlArtifactSourceName] = useState<
		string | null
	>(null);
	const [sqlArtifactSourceFileName, setSqlArtifactSourceFileName] = useState<
		string | null
	>(null);
	const [selectedSource, setSelectedSource] = useState<SourceFile | null>(null);
	const [skills, setSkills] = useState<SkillEntry[]>([]);
	const [selectedSkill, setSelectedSkill] = useState<SkillEntry | null>(null);
	const [selectedSkillResource, setSelectedSkillResource] =
		useState<SkillResourceEntry | null>(null);
	const [skillEditorText, setSkillEditorText] = useState("");
	const [savedSkillText, setSavedSkillText] = useState("");
	const [skillResourceEditorText, setSkillResourceEditorText] = useState("");
	const [savedSkillResourceText, setSavedSkillResourceText] = useState("");
	const [isRunningSql, setIsRunningSql] = useState(false);
	const [isPreviewingSource, setIsPreviewingSource] = useState(false);
	const [isPreviewingSqlArtifact, setIsPreviewingSqlArtifact] = useState(false);
	const [uploadStatus, setUploadStatus] = useState("");
	const {
		isExplorerCollapsed,
		setIsExplorerCollapsed,
		shellStyle,
		startPanelResize,
		startVerticalResize,
	} = usePaneLayout({ centerRef, sql });

	const hydrate = useCallback(async () => {
		const [healthResult, skillsResult] = await Promise.allSettled([
			fetchJson<HealthPayload>("/api/health"),
			fetchJson<{ skills?: SkillEntry[] }>("/api/skills"),
		]);
		if (healthResult.status === "fulfilled") {
			setSelectedModel(healthResult.value.model);
		}
		if (
			skillsResult.status === "fulfilled" &&
			skillsResult.value.skills?.length
		) {
			const firstSkill = skillsResult.value.skills[0];
			const firstSkillContent = skillContent(firstSkill);
			setSkills(skillsResult.value.skills);
			setSelectedSkill(firstSkill);
			setSkillEditorText(firstSkillContent);
			setSavedSkillText(firstSkillContent);
		}

		try {
			const bootstrapPayload =
				await fetchJson<BootstrapPayload>("/api/bootstrap");
			setBootstrap(bootstrapPayload);
			setSql(bootstrapPayload.sample_sql);
			setSqlResult(bootstrapPayload.initial_result || null);
			setSourcePreviewResult(null);
			setSqlArtifactPreviewResult(null);
			setSqlArtifactSourceName(null);
			setSqlArtifactSourceFileName(null);
			setSelectedSkillResource(null);
			setSelectedSource(bootstrapPayload.source_files[0] || null);
			setSelectedSqlArtifact(
				preferredSqlArtifact(bootstrapPayload.sql_artifacts),
			);
		} catch (error) {
			setBootstrap(emptyBootstrap);
			setSql(emptyBootstrap.sample_sql);
			setSqlResult({
				status: "error",
				message: `Workbench API failed: ${(error as Error).message}`,
			});
		}
	}, []);

	const refreshExplorerData = useCallback(async () => {
		try {
			const payload = await fetchJson<BootstrapPayload>("/api/bootstrap");
			setBootstrap(payload);
			setSelectedSource((currentSource) =>
				currentSource
					? payload.source_files.find(
							(source) => source.id === currentSource.id,
						) ||
						payload.source_files[0] ||
						null
					: payload.source_files[0] || null,
			);
			setSelectedSqlArtifact((currentSqlArtifact) =>
				currentSqlArtifact
					? payload.sql_artifacts.find(
							(sqlArtifact) => sqlArtifact.name === currentSqlArtifact.name,
						) || preferredSqlArtifact(payload.sql_artifacts)
					: preferredSqlArtifact(payload.sql_artifacts),
			);
		} catch {
			// Keep the current explorer state when a transient refresh races the backend.
		}
	}, []);

	useEffect(() => {
		hydrate();
	}, [hydrate]);

	useEffect(() => {
		setThemeMode(preferredThemeMode());
	}, []);

	useEffect(() => {
		if (!didSkipInitialThemePersist.current) {
			didSkipInitialThemePersist.current = true;
			return;
		}
		window.localStorage.setItem(themeStorageKey, themeMode);
	}, [themeMode]);

	const runSql = useCallback(async () => {
		setIsRunningSql(true);
		setInspectorView("results");
		setSelectedSkillResource(null);
		setSourcePreviewResult(null);
		try {
			const result = await fetchJson<SqlResult>("/api/sql/run", {
				method: "POST",
				body: JSON.stringify({ sql, max_rows: previewRowLimit }),
			});
			setSqlResult(result);
		} catch (error) {
			setSqlResult({
				status: "error",
				message: (error as Error).message,
			});
		} finally {
			setIsRunningSql(false);
		}
	}, [sql]);

	const setExplorerSection = useCallback((section: ExplorerKey) => {
		setActiveExplorer(section);
		setExplorerRailMode(section);
	}, []);

	const markExplorerSelection = useCallback((section: ExplorerKey) => {
		setActiveExplorer(section);
	}, []);

	const showExplorerSection = useCallback(
		(section: ExplorerKey) => {
			setExplorerSection(section);
			setSidePanel("explorer");
			setIsExplorerCollapsed(false);
			setExplorerJumpToken((token) => token + 1);
		},
		[setExplorerSection, setIsExplorerCollapsed],
	);

	const expandExplorer = useCallback(() => {
		setExplorerRailMode("all");
		setSidePanel("explorer");
		setIsExplorerCollapsed(false);
		setExplorerJumpToken((token) => token + 1);
	}, [setIsExplorerCollapsed]);

	const openSettings = useCallback(() => {
		if (sidePanel === "settings" && !isExplorerCollapsed) {
			setIsExplorerCollapsed(true);
			return;
		}
		setSidePanel("settings");
		setIsExplorerCollapsed(false);
	}, [isExplorerCollapsed, setIsExplorerCollapsed, sidePanel]);

	const toggleSidePanel = useCallback(() => {
		setIsExplorerCollapsed((collapsed) => !collapsed);
	}, [setIsExplorerCollapsed]);

	const toggleAgentPanel = useCallback(() => {
		setIsAgentPanelCollapsed((collapsed) => !collapsed);
	}, []);

	const toggleThemeMode = useCallback(() => {
		setThemeMode((mode) => (mode === "dark" ? "light" : "dark"));
	}, []);

	const selectSqlArtifact = useCallback(
		async (sqlArtifact: SqlArtifact) => {
			const requestId = sqlArtifactPreviewRequestId.current + 1;
			sqlArtifactPreviewRequestId.current = requestId;
			const isView = isSqlArtifactView(sqlArtifact);
			const explorerSection = isView ? "views" : "sql";
			setSelectedSqlArtifact(sqlArtifact);
			setSelectedSkillResource(null);
			markExplorerSelection(explorerSection);
			setInspectorView("sqlArtifact");
			setIsPreviewingSqlArtifact(true);
			setSourcePreviewResult(null);
			setSqlArtifactPreviewResult(null);
			setSqlArtifactSourceName(null);
			setSqlArtifactSourceFileName(firstSourceFileName(sqlArtifact));
			try {
				const [result, details] = await Promise.all([
					fetchJson<SqlResult>("/api/sql/run", {
						method: "POST",
						body: JSON.stringify({
							sql: sqlArtifactPreviewSql(sqlArtifact.name),
							max_rows: previewRowLimit,
						}),
					}),
					fetchSqlArtifactDetails(sqlArtifact.name).catch(() => null),
				]);
				if (sqlArtifactPreviewRequestId.current === requestId) {
					setSelectedSqlArtifact(
						details ? { ...sqlArtifact, ...details } : sqlArtifact,
					);
					setSqlArtifactPreviewResult(result);
					setSqlArtifactSourceFileName(
						firstSourceFileName(details) || firstSourceFileName(sqlArtifact),
					);
					if (isView && details?.create_sql) {
						setSql(
							queryFromCreateViewSql(details.create_sql, sqlArtifact.name),
						);
						setSqlArtifactSourceName(
							details.source_sql_artifact_names?.[0] || null,
						);
					}
				}
			} catch (error) {
				if (sqlArtifactPreviewRequestId.current === requestId) {
					setSqlArtifactPreviewResult({
						status: "error",
						message: (error as Error).message,
					});
					setSqlArtifactSourceName(null);
					setSqlArtifactSourceFileName(null);
				}
			} finally {
				if (sqlArtifactPreviewRequestId.current === requestId) {
					setIsPreviewingSqlArtifact(false);
				}
			}
		},
		[markExplorerSelection],
	);

	const selectSource = useCallback(
		async (source: SourceFile) => {
			const requestId = sourcePreviewRequestId.current + 1;
			sourcePreviewRequestId.current = requestId;
			setSelectedSource(source);
			setSelectedSkillResource(null);
			setSqlArtifactSourceName(null);
			setSqlArtifactSourceFileName(null);
			setSqlArtifactPreviewResult(null);
			markExplorerSelection("files");
			setInspectorView("source");
			const previewPath = source.source_path || source.destination_path || "";
			const sourceKind = source.kind.toLowerCase();
			const sourceName = (source.source_path || source.name).toLowerCase();
			const isTabularPreview =
				sourceKind === "csv" ||
				sourceKind === "xlsx" ||
				sourceName.endsWith(".csv") ||
				sourceName.endsWith(".xlsx");
			if (!previewPath || !isTabularPreview) {
				setSourcePreviewResult(null);
				setIsPreviewingSource(false);
				return;
			}
			setSourcePreviewResult(null);
			setIsPreviewingSource(true);
			try {
				const result = await fetchJson<SqlResult>("/api/files/preview", {
					method: "POST",
					body: JSON.stringify({
						path: previewPath,
						max_rows: previewRowLimit,
						sheet: source.sheet_name || undefined,
					}),
				});
				if (sourcePreviewRequestId.current === requestId) {
					setSourcePreviewResult(result);
				}
			} catch (error) {
				if (sourcePreviewRequestId.current === requestId) {
					setSourcePreviewResult({
						status: "error",
						message: (error as Error).message,
					});
				}
			} finally {
				if (sourcePreviewRequestId.current === requestId) {
					setIsPreviewingSource(false);
				}
			}
		},
		[markExplorerSelection],
	);

	const selectSkill = useCallback(
		(skill: SkillEntry) => {
			const nextContent = skillContent(skill);
			setSelectedSkill(skill);
			setSelectedSkillResource(null);
			setSourcePreviewResult(null);
			setSqlArtifactSourceName(null);
			setSqlArtifactSourceFileName(null);
			setSkillEditorText(nextContent);
			setSavedSkillText(nextContent);
			markExplorerSelection("skills");
			setInspectorView("skill");
		},
		[markExplorerSelection],
	);

	const selectSkillResource = useCallback(
		(resource: SkillResourceEntry) => {
			const ownerSkill = skills.find(
				(skill) => skill.name === resource.skillName,
			);
			if (ownerSkill && selectedSkill?.name !== ownerSkill.name) {
				const nextContent = skillContent(ownerSkill);
				setSelectedSkill(ownerSkill);
				setSkillEditorText(nextContent);
				setSavedSkillText(nextContent);
			}
			setSelectedSkillResource(resource);
			setSourcePreviewResult(null);
			setSqlArtifactSourceName(null);
			setSqlArtifactSourceFileName(null);
			markExplorerSelection("skills");
			setInspectorView("skillResource");
			setSkillResourceEditorText(resource.content || "");
			setSavedSkillResourceText(resource.content || "");
		},
		[markExplorerSelection, selectedSkill?.name, skills],
	);

	const saveSkill = useCallback(async () => {
		if (!selectedSkill) {
			return;
		}
		const savedSkill = await fetchJson<SkillEntry>("/api/skills/save", {
			method: "POST",
			body: JSON.stringify({
				name: selectedSkill.name,
				content: skillEditorText,
			}),
		});
		setSavedSkillText(skillEditorText);
		const savedSkillPatch = {
			content: skillEditorText,
			modified_at: savedSkill.modified_at,
		};
		setSelectedSkill((currentSkill) =>
			currentSkill ? { ...currentSkill, ...savedSkillPatch } : currentSkill,
		);
		setSkills((currentSkills) =>
			currentSkills.map((skill) =>
				skill.name === selectedSkill.name
					? { ...skill, ...savedSkillPatch }
					: skill,
			),
		);
	}, [selectedSkill, skillEditorText]);

	const revertSkill = useCallback(() => {
		setSkillEditorText(savedSkillText);
	}, [savedSkillText]);

	const saveSelectedSkillResource = useCallback(async () => {
		if (!selectedSkillResource) {
			return;
		}
		const path =
			selectedSkillResource.path || selectedSkillResource.relative_path || "";
		if (!path) {
			return;
		}
		const savedResource = await saveSkillResource({
			path,
			content: skillResourceEditorText,
		});
		const updatedResource: SkillResourceEntry = {
			...selectedSkillResource,
			content: savedResource.content,
			path: savedResource.path,
			relative_path: savedResource.relative_path,
		};
		setSelectedSkillResource(updatedResource);
		setSavedSkillResourceText(savedResource.content);
		setSkills((currentSkills) =>
			currentSkills.map((skill) =>
				skill.name === updatedResource.skillName
					? updateSkillResourceInSkill(skill, updatedResource)
					: skill,
			),
		);
		if (updatedResource.group === "instructions") {
			setSelectedSkill((currentSkill) =>
				currentSkill?.name === updatedResource.skillName
					? updateSkillResourceInSkill(currentSkill, updatedResource)
					: currentSkill,
			);
			setSkillEditorText(savedResource.content);
			setSavedSkillText(savedResource.content);
		}
	}, [selectedSkillResource, skillResourceEditorText]);

	const revertSelectedSkillResource = useCallback(() => {
		setSkillResourceEditorText(savedSkillResourceText);
	}, [savedSkillResourceText]);

	const applyBootstrap = useCallback((payload: BootstrapPayload) => {
		setBootstrap(payload);
		setSql(payload.sample_sql);
		setSqlResult(payload.initial_result || null);
		setSourcePreviewResult(null);
		setSqlArtifactPreviewResult(null);
		setSqlArtifactSourceName(null);
		setSqlArtifactSourceFileName(null);
		setSelectedSkillResource(null);
		setSelectedSource(payload.source_files[0] || null);
		setSelectedSqlArtifact(preferredSqlArtifact(payload.sql_artifacts));
	}, []);

	const uploadFiles = useCallback(
		async (files: File[]): Promise<UploadedWorkspaceFile[]> => {
			const selectedFiles = files.filter((file) => file.size > 0);
			if (selectedFiles.length === 0) {
				return [];
			}
			try {
				let latestBootstrap: BootstrapPayload | undefined;
				const uploadedFiles: UploadedWorkspaceFile[] = [];
				for (const [fileIndex, file] of selectedFiles.entries()) {
					const progress =
						selectedFiles.length > 1
							? `${fileIndex + 1}/${selectedFiles.length} `
							: "";
					setUploadStatus(`Uploading ${progress}${file.name}`);
					const payload = await uploadWorkspaceFile(file);
					const uploadedFile = uploadedWorkspaceFile(payload, file);
					if (uploadedFile) {
						uploadedFiles.push(uploadedFile);
					}
					if (payload.bootstrap) {
						latestBootstrap = payload.bootstrap;
					}
				}
				if (latestBootstrap) {
					applyBootstrap(latestBootstrap);
				}
				setActiveExplorer("files");
				setExplorerRailMode("all");
				setSidePanel("explorer");
				setIsExplorerCollapsed(false);
				setUploadStatus("");
				return uploadedFiles;
			} catch (error) {
				setUploadStatus(`Upload failed: ${(error as Error).message}`);
				throw error;
			}
		},
		[applyBootstrap, setIsExplorerCollapsed],
	);

	const startExplorerResize = useCallback(
		(event: ReactPointerEvent) => startPanelResize("explorer", event),
		[startPanelResize],
	);

	const startChatResize = useCallback(
		(event: ReactPointerEvent) => startPanelResize("chat", event),
		[startPanelResize],
	);

	return (
		<main
			ref={shellRef}
			className={[
				"workbench-shell",
				isExplorerCollapsed ? "explorer-collapsed" : "",
				isAgentPanelCollapsed ? "agent-collapsed" : "",
			]
				.filter(Boolean)
				.join(" ")}
			style={{
				...shellStyle,
				...workbenchScaleStyle(uiScale),
			}}
			data-theme={themeMode}
		>
			<header className="top-bar">
				<div className="brand-lockup">
					<BrandMark />
					<div>
						<span className="eyebrow">DATA AGENTICS</span>
						<h1>Workbench</h1>
					</div>
				</div>
				<div className="top-actions">
					<button
						className={
							isAgentPanelCollapsed
								? "agent-toggle-button"
								: "agent-toggle-button active"
						}
						type="button"
						aria-label={
							isAgentPanelCollapsed ? "Show agent panel" : "Hide agent panel"
						}
						aria-expanded={!isAgentPanelCollapsed}
						onClick={toggleAgentPanel}
					>
						<FaRobot aria-hidden="true" className="agent-icon" size={17} />
					</button>
				</div>
			</header>

			<ActivityBar
				activeMode={explorerRailMode}
				isExplorerCollapsed={isExplorerCollapsed}
				sidePanel={sidePanel}
				themeMode={themeMode}
				onExpandExplorer={expandExplorer}
				onOpenSettings={openSettings}
				onSelectExplorer={showExplorerSection}
				onToggleTheme={toggleThemeMode}
			/>

			{sidePanel === "settings" ? (
				<SettingsPanel
					isCollapsed={isExplorerCollapsed}
					rounding={rounding}
					uiScale={uiScale}
					onRoundingChange={setRounding}
					onUiScaleChange={setUiScale}
					onToggle={toggleSidePanel}
				/>
			) : (
				<ExplorerPanel
					activeExplorer={activeExplorer}
					bootstrap={bootstrap}
					inspectorView={inspectorView}
					isCollapsed={isExplorerCollapsed}
					jumpToken={explorerJumpToken}
					railMode={explorerRailMode}
					selectedSkill={selectedSkill}
					selectedSkillResource={selectedSkillResource}
					selectedSource={selectedSource}
					selectedSqlArtifact={selectedSqlArtifact}
					skills={skills}
					onSelectSkill={selectSkill}
					onSelectSkillResource={selectSkillResource}
					onSelectSource={selectSource}
					onSelectSqlArtifact={selectSqlArtifact}
					onToggle={toggleSidePanel}
				/>
			)}

			<button
				className="resize-handle explorer-handle"
				onPointerDown={startExplorerResize}
				type="button"
				aria-label="Resize explorer"
			/>

			<WorkspacePanel
				bootstrap={bootstrap}
				centerRef={centerRef}
				inspectorView={inspectorView}
				isPreviewingSqlArtifact={isPreviewingSqlArtifact}
				isPreviewingSource={isPreviewingSource}
				isRunningSql={isRunningSql}
				isQueryVisible={shouldShowQueryPane(inspectorView, selectedSqlArtifact)}
				rounding={rounding}
				themeMode={themeMode}
				selectedSkill={selectedSkill}
				selectedSkillResource={selectedSkillResource}
				selectedModel={selectedModel}
				selectedSource={selectedSource}
				selectedSqlArtifact={selectedSqlArtifact}
				sourcePreviewResult={sourcePreviewResult}
				sqlArtifactSourceFileName={sqlArtifactSourceFileName}
				sqlArtifactSourceName={sqlArtifactSourceName}
				isSkillResourceSaved={
					skillResourceEditorText === savedSkillResourceText
				}
				isSkillSaved={skillEditorText === savedSkillText}
				skillResourceText={skillResourceEditorText}
				skillText={skillEditorText}
				sql={sql}
				sqlResult={sqlResult}
				sqlArtifactPreviewResult={sqlArtifactPreviewResult}
				onRunSql={runSql}
				onRevertSkillResource={revertSelectedSkillResource}
				onRevertSkill={revertSkill}
				onSaveSkillResource={saveSelectedSkillResource}
				onSaveSkill={saveSkill}
				onSkillResourceTextChange={setSkillResourceEditorText}
				onSkillTextChange={setSkillEditorText}
				onSqlChange={setSql}
				onVerticalResize={startVerticalResize}
			/>

			<button
				className="resize-handle chat-handle"
				onPointerDown={startChatResize}
				type="button"
				aria-label="Resize chat"
			/>
			<AgentPanel
				bootstrapSourceFiles={bootstrap.source_files}
				isCollapsed={isAgentPanelCollapsed}
				modelOptions={modelOptions}
				selectedModel={selectedModel}
				skills={skills}
				sqlArtifacts={bootstrap.sql_artifacts}
				uploadStatus={uploadStatus}
				onChatSettled={refreshExplorerData}
				onModelChange={setSelectedModel}
				onRunSql={runSql}
				onToggle={toggleAgentPanel}
				onUploadFiles={uploadFiles}
			/>
		</main>
	);
}

function preferredSqlArtifact(sqlArtifacts: SqlArtifact[]) {
	return (
		sqlArtifacts.find(
			(sqlArtifact) => sqlArtifact.name === "analysis_result",
		) ||
		sqlArtifacts.find((sqlArtifact) => sqlArtifact.name.endsWith("_typed")) ||
		sqlArtifacts[0] ||
		null
	);
}

function sqlArtifactPreviewSql(sqlArtifactName: string) {
	return `SELECT * FROM ${quoteSqlIdentifier(sqlArtifactName)};`;
}

function quoteSqlIdentifier(identifier: string) {
	return `"${identifier.replaceAll('"', '""')}"`;
}

function fetchSqlArtifactDetails(sqlArtifactName: string) {
	return fetchJson<SqlArtifactDetails>(
		`/api/sql/sql-artifacts/${encodeURIComponent(sqlArtifactName)}`,
	);
}

function updateSkillResourceInSkill(
	skill: SkillEntry,
	resource: SkillResourceEntry,
): SkillEntry {
	if (resource.group === "instructions") {
		return {
			...skill,
			content: resource.content,
			instructions: {
				...(skill.instructions || {}),
				content: resource.content,
				path: resource.path,
				relative_path: resource.relative_path,
			},
		};
	}
	return {
		...skill,
		[resource.group]: updateSkillResourceList(skill[resource.group], resource),
	};
}

function updateSkillResourceList(
	resources: SkillResourcePayload[] | undefined,
	updatedResource: SkillResourceEntry,
) {
	return (resources || []).map((resource) =>
		skillResourcePathKey(resource) === skillResourcePathKey(updatedResource)
			? {
					...resource,
					content: updatedResource.content,
					path: updatedResource.path,
					relative_path: updatedResource.relative_path,
				}
			: resource,
	);
}

function skillResourcePathKey(resource: SkillResourcePayload) {
	return resource.path || resource.relative_path || "";
}

function firstSourceFileName(sqlArtifact: SqlArtifact | null) {
	return (
		sqlArtifact?.source_file_names?.[0] ||
		sqlArtifact?.source_references?.[0]?.name ||
		null
	);
}

function queryFromCreateViewSql(createSql: string, sqlArtifactName: string) {
	const trimmedSql = createSql.trim();
	const createViewPrefix =
		/^CREATE\s+(?:TEMP(?:ORARY)?\s+)?VIEW(?:\s+IF\s+NOT\s+EXISTS)?\s+(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|\S+)\s+AS\s+/i;
	const match = createViewPrefix.exec(trimmedSql);
	if (!match) {
		return sqlArtifactPreviewSql(sqlArtifactName);
	}
	const query = trimmedSql
		.slice(match[0].length)
		.trim()
		.replace(/;+\s*$/, "");
	return query ? `${query};` : sqlArtifactPreviewSql(sqlArtifactName);
}

function uploadedWorkspaceFile(
	payload: UploadPayload,
	file: File,
): UploadedWorkspaceFile | null {
	const upload = payload.upload;
	if (!upload?.path) {
		return null;
	}
	return {
		name: upload.name || file.name || upload.path,
		path: upload.path,
		contentType: upload.content_type || file.type || undefined,
		artifactBackend: upload.artifact_backend,
	};
}

function shouldShowQueryPane(
	inspectorView: InspectorView,
	selectedSqlArtifact: SqlArtifact | null,
) {
	if (inspectorView === "results") {
		return true;
	}
	return inspectorView === "sqlArtifact" && selectedSqlArtifact
		? isSqlArtifactView(selectedSqlArtifact)
		: false;
}
