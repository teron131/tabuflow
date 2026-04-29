import { Minus, PanelLeft, Plus } from "lucide-react";
import { memo } from "react";
import { clampWorkbenchScale, workbenchScale } from "./ui-scale";

type SettingsPanelProps = {
	isCollapsed: boolean;
	uiScale: number;
	onUiScaleChange: (scale: number) => void;
	onToggle: () => void;
};

export const SettingsPanel = memo(function SettingsPanel({
	isCollapsed,
	uiScale,
	onUiScaleChange,
	onToggle,
}: SettingsPanelProps) {
	const scalePercent = Math.round(uiScale * 100);
	const setScale = (scale: number) =>
		onUiScaleChange(clampWorkbenchScale(scale));

	return (
		<aside className={isCollapsed ? "explorer collapsed" : "explorer"}>
			<header className="panel-title">
				<span>SETTINGS</span>
				<button
					className="panel-toggle"
					type="button"
					aria-label={isCollapsed ? "Expand settings" : "Collapse settings"}
					aria-expanded={!isCollapsed}
					onClick={onToggle}
				>
					<PanelLeft size={14} />
				</button>
			</header>
			<div className="settings-panel">
				<section className="setting-group">
					<header>
						<span>Interface</span>
						<strong>{scalePercent}%</strong>
					</header>
					<label className="scale-control">
						<span>UI scale</span>
						<input
							aria-label="Workbench UI scale"
							type="range"
							min={workbenchScale.min}
							max={workbenchScale.max}
							step={workbenchScale.step}
							value={uiScale}
							onChange={(event) => setScale(Number(event.target.value))}
						/>
					</label>
					<div className="stepper-row">
						<button
							type="button"
							aria-label="Decrease UI scale"
							onClick={() => setScale(uiScale - workbenchScale.step)}
						>
							<Minus size={13} />
						</button>
						<button
							type="button"
							aria-label="Increase UI scale"
							onClick={() => setScale(uiScale + workbenchScale.step)}
						>
							<Plus size={13} />
						</button>
					</div>
				</section>
			</div>
		</aside>
	);
});
