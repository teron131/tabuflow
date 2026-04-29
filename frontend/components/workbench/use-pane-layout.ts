import {
	type PointerEvent as ReactPointerEvent,
	type RefObject,
	useCallback,
	useState,
} from "react";
import { clamp, queryContentHeight } from "./sql";
import type { ShellStyle } from "./types";

const DEFAULT_EXPLORER_WIDTH = 286;
const DEFAULT_CHAT_WIDTH = 470;
const DEFAULT_QUERY_HEIGHT = 30;

export function usePaneLayout({
	centerRef,
	sql,
}: {
	centerRef: RefObject<HTMLElement | null>;
	sql: string;
}) {
	const [isExplorerCollapsed, setIsExplorerCollapsed] = useState(false);
	const [explorerWidth, setExplorerWidth] = useState(DEFAULT_EXPLORER_WIDTH);
	const [chatWidth, setChatWidth] = useState(DEFAULT_CHAT_WIDTH);
	const [queryHeight, setQueryHeight] = useState(DEFAULT_QUERY_HEIGHT);
	const queryMinHeight = queryContentHeight(sql);

	const shellStyle: ShellStyle = {
		"--explorer-width": `${explorerWidth}px`,
		"--chat-width": `${chatWidth}px`,
		"--query-height": `${queryHeight}%`,
		"--query-min-height": `${queryMinHeight}px`,
	};

	const startHorizontalResize = useCallback(
		(kind: "explorer" | "chat", event: ReactPointerEvent) => {
			event.preventDefault();
			event.currentTarget.setPointerCapture(event.pointerId);
			const viewportWidth = window.innerWidth;

			function resize(moveEvent: PointerEvent) {
				if (kind === "explorer") {
					setExplorerWidth(clamp(moveEvent.clientX - 50, 220, 430));
					return;
				}
				setChatWidth(
					clamp(
						viewportWidth - moveEvent.clientX - 8,
						360,
						Math.min(720, viewportWidth * 0.48),
					),
				);
			}

			function stopResize() {
				window.removeEventListener("pointermove", resize);
				window.removeEventListener("pointerup", stopResize);
			}

			window.addEventListener("pointermove", resize);
			window.addEventListener("pointerup", stopResize, { once: true });
		},
		[],
	);

	const startVerticalResize = useCallback(
		(event: ReactPointerEvent) => {
			event.preventDefault();
			event.currentTarget.setPointerCapture(event.pointerId);
			const rect = centerRef.current?.getBoundingClientRect();
			if (!rect) return;
			const centerRect = rect;
			const minPercent = clamp(
				(queryMinHeight / centerRect.height) * 100,
				14,
				42,
			);

			function resize(moveEvent: PointerEvent) {
				setQueryHeight(
					clamp(
						((moveEvent.clientY - centerRect.top) / centerRect.height) * 100,
						minPercent,
						76,
					),
				);
			}

			function stopResize() {
				window.removeEventListener("pointermove", resize);
				window.removeEventListener("pointerup", stopResize);
			}

			window.addEventListener("pointermove", resize);
			window.addEventListener("pointerup", stopResize, { once: true });
		},
		[centerRef, queryMinHeight],
	);

	return {
		isExplorerCollapsed,
		setIsExplorerCollapsed,
		shellStyle,
		startHorizontalResize,
		startVerticalResize,
	};
}
