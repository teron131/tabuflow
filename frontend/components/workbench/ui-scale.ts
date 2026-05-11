import type { ShellStyle } from "./types";

type WorkbenchScaleStyle = Partial<ShellStyle> & {
	"--workbench-ui-scale": string;
};

export const workbenchScale = {
	default: 1.1,
	min: 0.75,
	max: 1.5,
	step: 0.05,
};

export function clampWorkbenchScale(scale: number) {
	const boundedScale = Math.min(
		workbenchScale.max,
		Math.max(workbenchScale.min, scale),
	);
	return Number(boundedScale.toFixed(2));
}

export function workbenchScaleStyle(scale: number): WorkbenchScaleStyle {
	const safeScale = clampWorkbenchScale(scale);
	return {
		"--workbench-ui-scale": String(safeScale),
		width: `${100 / safeScale}vw`,
		height: `${100 / safeScale}dvh`,
		transform: `scale(${safeScale})`,
		transformOrigin: "top left",
	};
}
