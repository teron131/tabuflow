"use client";

import {
	type PointerEvent as ReactPointerEvent,
	useCallback,
	useEffect,
	useRef,
	useState,
} from "react";
import { ChatRail } from "@/components/chat-rail";
import { ActivityBar } from "@/components/workbench/activity-bar";
import { BrandMark } from "@/components/workbench/brand-mark";
import {
	defaultRounding,
	modelOptions,
} from "@/components/workbench/constants";
import { ExplorerPanel } from "@/components/workbench/explorer-panel";
import { SettingsPanel } from "@/components/workbench/settings-panel";
import { isTargetView } from "@/components/workbench/targets";
import type {
	ExplorerKey,
	InspectorView,
	RoundingSettings,
	SidePanel,
} from "@/components/workbench/types";
import {
	workbenchScale,
	workbenchScaleStyle,
} from "@/components/workbench/ui-scale";
import { usePaneLayout } from "@/components/workbench/use-pane-layout";
import { WorkspacePanel } from "@/components/workbench/workspace-panel";
import {
	type BootstrapPayload,
	emptyBootstrap,
	fetchJson,
	type HealthPayload,
	type SkillEntry,
	type SourceFile,
	type SqlResult,
	skillContent,
	type Target,
	type TargetDetails,
	type UploadPayload,
	uploadWorkspaceFile,
} from "@/lib/api";

export type UploadedWorkspaceFile = {
	name: string;
	path: string;
	contentType?: string;
	targetBackend?: string;
};

export function Workbench() {
	const shellRef = useRef<HTMLElement | null>(null);
	const centerRef = useRef<HTMLElement | null>(null);
	const targetPreviewRequestId = useRef(0);
	const [activeExplorer, setActiveExplorer] = useState<ExplorerKey>("sql");
	const [sidePanel, setSidePanel] = useState<SidePanel>("explorer");
	const [inspectorView, setInspectorView] = useState<InspectorView>("results");
	const [rounding, setRounding] = useState<RoundingSettings>(defaultRounding);
	const [uiScale, setUiScale] = useState(workbenchScale.default);
	const [selectedModel, setSelectedModel] = useState("gpt-5.4-nano");
	const [bootstrap, setBootstrap] = useState<BootstrapPayload>(emptyBootstrap);
	const [sql, setSql] = useState(emptyBootstrap.sample_sql);
	const [sqlResult, setSqlResult] = useState<SqlResult | null>(null);
	const [targetPreviewResult, setTargetPreviewResult] =
		useState<SqlResult | null>(null);
	const [selectedTarget, setSelectedTarget] = useState<Target | null>(null);
	const [targetSourceName, setTargetSourceName] = useState<string | null>(null);
	const [targetSourceFileName, setTargetSourceFileName] = useState<
		string | null
	>(null);
	const [selectedSource, setSelectedSource] = useState<SourceFile | null>(null);
	const [skills, setSkills] = useState<SkillEntry[]>([]);
	const [selectedSkill, setSelectedSkill] = useState<SkillEntry | null>(null);
	const [skillEditorText, setSkillEditorText] = useState("");
	const [savedSkillText, setSavedSkillText] = useState("");
	const [isRunningSql, setIsRunningSql] = useState(false);
	const [isPreviewingTarget, setIsPreviewingTarget] = useState(false);
	const [uploadStatus, setUploadStatus] = useState("");
	const {
		isExplorerCollapsed,
		setIsExplorerCollapsed,
		shellStyle,
		startPanelResize,
		startVerticalResize,
	} = usePaneLayout({ centerRef, sql });

	const hydrate = useCallback(async () => {
		try {
			const [healthPayload, bootstrapPayload, skillsPayload] =
				await Promise.all([
					fetchJson<HealthPayload>("/api/health"),
					fetchJson<BootstrapPayload>("/api/bootstrap"),
					fetchJson<{ skills?: SkillEntry[] }>("/api/skills"),
				]);
			setBootstrap(bootstrapPayload);
			setSql(bootstrapPayload.sample_sql);
			setSqlResult(bootstrapPayload.initial_result || null);
			setTargetPreviewResult(null);
			setTargetSourceName(null);
			setTargetSourceFileName(null);
			setSelectedModel(healthPayload.model);
			setSelectedSource(bootstrapPayload.source_files[0] || null);
			setSelectedTarget(preferredTarget(bootstrapPayload.targets));
			if (skillsPayload.skills?.length) {
				const firstSkill = skillsPayload.skills[0];
				const firstSkillContent = skillContent(firstSkill);
				setSkills(skillsPayload.skills);
				setSelectedSkill(firstSkill);
				setSkillEditorText(firstSkillContent);
				setSavedSkillText(firstSkillContent);
			}
		} catch (error) {
			setSqlResult({
				status: "error",
				message: `Workbench API failed: ${(error as Error).message}`,
			});
		}
	}, []);

	useEffect(() => {
		hydrate();
	}, [hydrate]);

	const runSql = useCallback(async () => {
		setIsRunningSql(true);
		setInspectorView("results");
		try {
			const result = await fetchJson<SqlResult>("/api/sql/run", {
				method: "POST",
				body: JSON.stringify({ sql, max_rows: 100 }),
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

	const toggleFiles = useCallback(() => {
		if (
			!isExplorerCollapsed &&
			sidePanel === "explorer" &&
			activeExplorer === "files"
		) {
			setIsExplorerCollapsed(true);
			return;
		}
		setActiveExplorer("files");
		setSidePanel("explorer");
		setIsExplorerCollapsed(false);
	}, [activeExplorer, isExplorerCollapsed, setIsExplorerCollapsed, sidePanel]);

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

	const selectTarget = useCallback(async (target: Target) => {
		const requestId = targetPreviewRequestId.current + 1;
		targetPreviewRequestId.current = requestId;
		const isView = isTargetView(target);
		setSelectedTarget(target);
		setActiveExplorer(isView ? "views" : "sql");
		setInspectorView("target");
		setIsPreviewingTarget(true);
		setTargetPreviewResult(null);
		setTargetSourceName(null);
		setTargetSourceFileName(firstSourceFileName(target));
		try {
			const [result, details] = await Promise.all([
				fetchJson<SqlResult>("/api/sql/run", {
					method: "POST",
					body: JSON.stringify({
						sql: targetPreviewSql(target.name),
						max_rows: 100,
					}),
				}),
				isView
					? fetchTargetDetails(target.name).catch(() => null)
					: Promise.resolve(null),
			]);
			if (targetPreviewRequestId.current === requestId) {
				setTargetPreviewResult(result);
				setTargetSourceFileName(
					firstSourceFileName(details) || firstSourceFileName(target),
				);
				if (details?.create_sql) {
					setSql(queryFromCreateViewSql(details.create_sql, target.name));
					setTargetSourceName(details.source_target_names?.[0] || null);
				}
			}
		} catch (error) {
			if (targetPreviewRequestId.current === requestId) {
				setTargetPreviewResult({
					status: "error",
					message: (error as Error).message,
				});
				setTargetSourceName(null);
				setTargetSourceFileName(null);
			}
		} finally {
			if (targetPreviewRequestId.current === requestId) {
				setIsPreviewingTarget(false);
			}
		}
	}, []);

	const selectSource = useCallback((source: SourceFile) => {
		setSelectedSource(source);
		setTargetSourceName(null);
		setTargetSourceFileName(null);
		setActiveExplorer("files");
		setInspectorView("source");
	}, []);

	const selectSkill = useCallback((skill: SkillEntry) => {
		const nextContent = skillContent(skill);
		setSelectedSkill(skill);
		setTargetSourceName(null);
		setTargetSourceFileName(null);
		setSkillEditorText(nextContent);
		setSavedSkillText(nextContent);
		setActiveExplorer("skills");
		setInspectorView("skill");
	}, []);

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

	const applyBootstrap = useCallback((payload: BootstrapPayload) => {
		setBootstrap(payload);
		setSql(payload.sample_sql);
		setSqlResult(payload.initial_result || null);
		setTargetPreviewResult(null);
		setTargetSourceName(null);
		setTargetSourceFileName(null);
		setSelectedSource(payload.source_files[0] || null);
		setSelectedTarget(preferredTarget(payload.targets));
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
			className={
				isExplorerCollapsed
					? "workbench-shell explorer-collapsed"
					: "workbench-shell"
			}
			style={{
				...shellStyle,
				...workbenchScaleStyle(uiScale),
			}}
		>
			<header className="top-bar">
				<div className="brand-lockup">
					<BrandMark />
					<div>
						<span className="eyebrow">DATA AGENTICS</span>
						<h1>Workbench</h1>
					</div>
				</div>
			</header>

			<ActivityBar
				activeExplorer={activeExplorer}
				isExplorerCollapsed={isExplorerCollapsed}
				sidePanel={sidePanel}
				onToggleFiles={toggleFiles}
				onOpenSettings={openSettings}
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
					isCollapsed={isExplorerCollapsed}
					selectedSkill={selectedSkill}
					selectedSource={selectedSource}
					selectedTarget={selectedTarget}
					skills={skills}
					onSelectSkill={selectSkill}
					onSelectSource={selectSource}
					onSelectTarget={selectTarget}
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
				isPreviewingTarget={isPreviewingTarget}
				isRunningSql={isRunningSql}
				rounding={rounding}
				selectedSkill={selectedSkill}
				selectedSource={selectedSource}
				selectedTarget={selectedTarget}
				targetSourceFileName={targetSourceFileName}
				targetSourceName={targetSourceName}
				isSkillSaved={skillEditorText === savedSkillText}
				skillText={skillEditorText}
				sql={sql}
				sqlResult={sqlResult}
				targetPreviewResult={targetPreviewResult}
				onRunSql={runSql}
				onRevertSkill={revertSkill}
				onSaveSkill={saveSkill}
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
			<ChatRail
				modelOptions={modelOptions}
				selectedModel={selectedModel}
				uploadStatus={uploadStatus}
				onModelChange={setSelectedModel}
				onUploadFiles={uploadFiles}
			/>
		</main>
	);
}

function preferredTarget(targets: Target[]) {
	return (
		targets.find((target) => target.name === "analysis_result") ||
		targets.find((target) => target.name.endsWith("_typed")) ||
		targets[0] ||
		null
	);
}

function targetPreviewSql(targetName: string) {
	return `SELECT * FROM ${quoteSqlIdentifier(targetName)} LIMIT 100;`;
}

function quoteSqlIdentifier(identifier: string) {
	return `"${identifier.replaceAll('"', '""')}"`;
}

function fetchTargetDetails(targetName: string) {
	return fetchJson<TargetDetails>(
		`/api/sql/targets/${encodeURIComponent(targetName)}`,
	);
}

function firstSourceFileName(target: Target | null) {
	return (
		target?.source_file_names?.[0] ||
		target?.source_references?.[0]?.name ||
		null
	);
}

function queryFromCreateViewSql(createSql: string, targetName: string) {
	const trimmedSql = createSql.trim();
	const createViewPrefix =
		/^CREATE\s+(?:TEMP(?:ORARY)?\s+)?VIEW(?:\s+IF\s+NOT\s+EXISTS)?\s+(?:"[^"]+"|`[^`]+`|\[[^\]]+\]|\S+)\s+AS\s+/i;
	const match = createViewPrefix.exec(trimmedSql);
	if (!match) {
		return targetPreviewSql(targetName);
	}
	const query = trimmedSql
		.slice(match[0].length)
		.trim()
		.replace(/;+\s*$/, "");
	return query ? `${query};` : targetPreviewSql(targetName);
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
		targetBackend: upload.target_backend,
	};
}
