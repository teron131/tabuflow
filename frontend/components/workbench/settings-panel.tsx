import { Minus, PanelLeft, Plus } from "lucide-react";
import { memo } from "react";
import { roundingDigits } from "./constants";
import type { RoundingSettings } from "./types";
import { clampWorkbenchScale, workbenchScale } from "./ui-scale";

type SettingsPanelProps = {
	isCollapsed: boolean;
	rounding: RoundingSettings;
	uiScale: number;
	onRoundingChange: (rounding: RoundingSettings) => void;
	onUiScaleChange: (scale: number) => void;
	onToggle: () => void;
};

export const SettingsPanel = memo(function SettingsPanel({
	isCollapsed,
	rounding,
	uiScale,
	onRoundingChange,
	onUiScaleChange,
	onToggle,
}: SettingsPanelProps) {
	const scalePercent = Math.round(uiScale * 100);
	const setScale = (scale: number) =>
		onUiScaleChange(clampWorkbenchScale(scale));
	const setRoundingEnabled = (enabled: boolean) =>
		onRoundingChange({ ...rounding, enabled });
	const setRoundingDigits = (digits: number) =>
		onRoundingChange({
			...rounding,
			digits: clampRoundingDigits(digits),
		});

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
				<section className="setting-group">
					<header>
						<span>Numbers</span>
						<strong>
							{rounding.enabled ? `${rounding.digits} decimals` : "Raw"}
						</strong>
					</header>
					<label className="toggle-row">
						<span>Round numbers</span>
						<input
							aria-label="Round table numbers"
							type="checkbox"
							checked={rounding.enabled}
							onChange={(event) => setRoundingEnabled(event.target.checked)}
						/>
					</label>
					<label className="scale-control">
						<span>Decimal places</span>
						<input
							aria-label="Selection stats decimal places"
							type="range"
							min={roundingDigits.min}
							max={roundingDigits.max}
							step={roundingDigits.step}
							value={rounding.digits}
							disabled={!rounding.enabled}
							onChange={(event) =>
								setRoundingDigits(Number(event.target.value))
							}
						/>
					</label>
					<div className="stepper-row">
						<button
							type="button"
							aria-label="Decrease decimal places"
							disabled={!rounding.enabled}
							onClick={() =>
								setRoundingDigits(rounding.digits - roundingDigits.step)
							}
						>
							<Minus size={13} />
						</button>
						<button
							type="button"
							aria-label="Increase decimal places"
							disabled={!rounding.enabled}
							onClick={() =>
								setRoundingDigits(rounding.digits + roundingDigits.step)
							}
						>
							<Plus size={13} />
						</button>
					</div>
				</section>
			</div>
		</aside>
	);
});

function clampRoundingDigits(digits: number) {
	return Math.min(roundingDigits.max, Math.max(roundingDigits.min, digits));
}
