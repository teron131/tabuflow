import {
	type PointerEvent as ReactPointerEvent,
	type RefObject,
	useCallback,
	useEffect,
	useState,
} from "react";
import { clamp, queryContentHeight } from "./sql";
import type { ShellStyle } from "./types";

const DEFAULT_EXPLORER_WIDTH = 240;
const DEFAULT_CHAT_WIDTH = 450;
const DEFAULT_CHAT_DOCK_HEIGHT = 250;
const DEFAULT_QUERY_HEIGHT = 30;
const COMPACT_BREAKPOINT = 980;
const EXPLORER_MIN_WIDTH = 220;
const EXPLORER_MAX_WIDTH = 430;
const COMPACT_WORKSPACE_RESERVE = 260;
const EXPLORER_LEFT_OFFSET = 50;
const CHAT_MIN_WIDTH = 360;
const CHAT_MAX_WIDTH = 720;
const CHAT_MAX_VIEWPORT_RATIO = 0.48;
const CHAT_HANDLE_OFFSET = 8;
const CHAT_DOCK_MIN_HEIGHT = 170;
const CHAT_DOCK_MAX_HEIGHT = 420;
const CHAT_DOCK_MAX_VIEWPORT_RATIO = 0.48;
const QUERY_MIN_PERCENT = 14;
const QUERY_MIN_PERCENT_MAX = 42;
const QUERY_MAX_PERCENT = 76;

type PanelResizeTarget = "explorer" | "chat";

function isCompactViewport(viewportWidth: number) {
	return viewportWidth <= COMPACT_BREAKPOINT;
}

function nextExplorerWidth(pointerX: number, viewportWidth: number) {
	const maxWidth = isCompactViewport(viewportWidth)
		? Math.max(
				EXPLORER_MIN_WIDTH,
				Math.min(EXPLORER_MAX_WIDTH, viewportWidth - COMPACT_WORKSPACE_RESERVE),
			)
		: EXPLORER_MAX_WIDTH;
	return clamp(pointerX - EXPLORER_LEFT_OFFSET, EXPLORER_MIN_WIDTH, maxWidth);
}

function nextChatWidth(pointerX: number, viewportWidth: number) {
	return clamp(
		viewportWidth - pointerX - CHAT_HANDLE_OFFSET,
		CHAT_MIN_WIDTH,
		Math.min(CHAT_MAX_WIDTH, viewportWidth * CHAT_MAX_VIEWPORT_RATIO),
	);
}

function nextChatDockHeight(pointerY: number, viewportHeight: number) {
	return clamp(
		viewportHeight - pointerY,
		CHAT_DOCK_MIN_HEIGHT,
		Math.min(
			CHAT_DOCK_MAX_HEIGHT,
			viewportHeight * CHAT_DOCK_MAX_VIEWPORT_RATIO,
		),
	);
}

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
	const [chatDockHeight, setChatDockHeight] = useState(
		DEFAULT_CHAT_DOCK_HEIGHT,
	);
	const [queryHeight, setQueryHeight] = useState(DEFAULT_QUERY_HEIGHT);
	const queryMinHeight = queryContentHeight(sql);

	useEffect(() => {
		if (isCompactViewport(window.innerWidth)) {
			setIsExplorerCollapsed(true);
		}
	}, []);

	const shellStyle: ShellStyle = {
		"--explorer-width": `${explorerWidth}px`,
		"--chat-width": `${chatWidth}px`,
		"--chat-dock-height": `${chatDockHeight}px`,
		"--query-height": `${queryHeight}%`,
		"--query-min-height": `${queryMinHeight}px`,
	};

	const startPanelResize = useCallback(
		(target: PanelResizeTarget, event: ReactPointerEvent) => {
			event.preventDefault();
			event.currentTarget.setPointerCapture(event.pointerId);
			const viewportWidth = window.innerWidth;
			const viewportHeight = window.innerHeight;
			const compactViewport = isCompactViewport(viewportWidth);

			function resize(moveEvent: PointerEvent) {
				if (target === "explorer") {
					setExplorerWidth(nextExplorerWidth(moveEvent.clientX, viewportWidth));
					return;
				}

				if (compactViewport) {
					setChatDockHeight(
						nextChatDockHeight(moveEvent.clientY, viewportHeight),
					);
					return;
				}

				setChatWidth(nextChatWidth(moveEvent.clientX, viewportWidth));
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
				QUERY_MIN_PERCENT,
				QUERY_MIN_PERCENT_MAX,
			);

			function resize(moveEvent: PointerEvent) {
				setQueryHeight(
					clamp(
						((moveEvent.clientY - centerRect.top) / centerRect.height) * 100,
						minPercent,
						QUERY_MAX_PERCENT,
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
		startPanelResize,
		startVerticalResize,
	};
}
