"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ChatRail } from "@/components/chat-rail";
import { ActivityBar } from "@/components/workbench/activity-bar";
import { BrandMark } from "@/components/workbench/brand-mark";
import { modelOptions } from "@/components/workbench/constants";
import { ExplorerPanel } from "@/components/workbench/explorer-panel";
import { SettingsPanel } from "@/components/workbench/settings-panel";
import { isTargetView } from "@/components/workbench/targets";
import type {
	CenterTab,
	ExplorerKey,
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
} from "@/lib/api";

export function Workbench() {
	const shellRef = useRef<HTMLElement | null>(null);
	const centerRef = useRef<HTMLElement | null>(null);
	const [activeExplorer, setActiveExplorer] = useState<ExplorerKey>("sql");
	const [sidePanel, setSidePanel] = useState<SidePanel>("explorer");
	const [activeTab, setActiveTab] = useState<CenterTab>("sql");
	const [uiScale, setUiScale] = useState(workbenchScale.default);
	const [selectedModel, setSelectedModel] = useState("gpt-5.4-nano");
	const [bootstrap, setBootstrap] = useState<BootstrapPayload>(emptyBootstrap);
	const [sql, setSql] = useState(emptyBootstrap.sample_sql);
	const [sqlResult, setSqlResult] = useState<SqlResult | null>(null);
	const [selectedTarget, setSelectedTarget] = useState<Target | null>(null);
	const [selectedSource, setSelectedSource] = useState<SourceFile | null>(null);
	const [skills, setSkills] = useState<SkillEntry[]>([]);
	const [selectedSkill, setSelectedSkill] = useState<SkillEntry | null>(null);
	const [skillDraft, setSkillDraft] = useState("");
	const [isRunningSql, setIsRunningSql] = useState(false);
	const {
		isExplorerCollapsed,
		setIsExplorerCollapsed,
		shellStyle,
		startHorizontalResize,
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
			setSelectedModel(healthPayload.model);
			setSelectedSource(bootstrapPayload.source_files[0] || null);
			setSelectedTarget(preferredTarget(bootstrapPayload.targets));
			if (skillsPayload.skills?.length) {
				const firstSkill = skillsPayload.skills[0];
				setSkills(skillsPayload.skills);
				setSelectedSkill(firstSkill);
				setSkillDraft(skillContent(firstSkill));
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

	async function runSql() {
		setIsRunningSql(true);
		setActiveTab("results");
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
	}

	function toggleFiles() {
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
	}

	function openSettings() {
		setSidePanel("settings");
		setIsExplorerCollapsed(false);
	}

	function toggleSidePanel() {
		setIsExplorerCollapsed((collapsed) => !collapsed);
	}

	function selectTarget(target: Target) {
		setSelectedTarget(target);
		setActiveExplorer(isTargetView(target) ? "views" : "sql");
		setActiveTab("target");
	}

	function selectSource(source: SourceFile) {
		setSelectedSource(source);
		setActiveExplorer("files");
		setActiveTab("source");
	}

	function selectSkill(skill: SkillEntry) {
		setSelectedSkill(skill);
		setSkillDraft(skillContent(skill));
		setActiveExplorer("skills");
		setActiveTab("skill");
	}

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
					uiScale={uiScale}
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
				onPointerDown={(event) => startHorizontalResize("explorer", event)}
				type="button"
				aria-label="Resize explorer"
			/>

			<WorkspacePanel
				activeTab={activeTab}
				bootstrap={bootstrap}
				centerRef={centerRef}
				isRunningSql={isRunningSql}
				selectedSkill={selectedSkill}
				selectedSource={selectedSource}
				selectedTarget={selectedTarget}
				skillDraft={skillDraft}
				sql={sql}
				sqlResult={sqlResult}
				onActiveTabChange={setActiveTab}
				onRunSql={runSql}
				onSkillDraftChange={setSkillDraft}
				onSqlChange={setSql}
				onVerticalResize={startVerticalResize}
			/>

			<button
				className="resize-handle chat-handle"
				onPointerDown={(event) => startHorizontalResize("chat", event)}
				type="button"
				aria-label="Resize chat"
			/>
			<ChatRail
				modelOptions={modelOptions}
				selectedModel={selectedModel}
				onModelChange={setSelectedModel}
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
