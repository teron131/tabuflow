import { memo, type ReactNode, useCallback, useMemo, useRef } from "react";

type EditorLine = {
	key: string;
	text: string;
};

const FALLBACK_EDITOR_LINE_HEIGHT = 19;

type CodeEditorProps = {
	ariaLabel?: string;
	className: string;
	highlightClassName: string;
	renderLine: (line: string) => ReactNode;
	value: string;
	wrap?: "soft" | "off";
	onChange: (value: string) => void;
};

export const CodeEditor = memo(function CodeEditor({
	ariaLabel,
	className,
	highlightClassName,
	renderLine,
	value,
	wrap = "soft",
	onChange,
}: CodeEditorProps) {
	const editorRef = useRef<HTMLDivElement | null>(null);
	const highlightRef = useRef<HTMLPreElement | null>(null);
	const activeEditorRef = useRef<HTMLTextAreaElement | null>(null);
	const activeLineFrameRef = useRef<number | null>(null);
	const lines = useMemo(() => editorLines(value), [value]);
	const renderedLines = useMemo(
		() =>
			lines.map((line, index) => (
				<span className="editor-line" key={line.key}>
					<span className="editor-line-number">{index + 1}</span>
					<span className="editor-line-text">
						{line.text ? renderLine(line.text) : "\u00a0"}
					</span>
				</span>
			)),
		[lines, renderLine],
	);

	const clearActiveLine = useCallback(() => {
		const container = editorRef.current;
		if (!container) {
			return;
		}
		delete container.dataset.activeLine;
	}, []);

	const scheduleActiveLineUpdate = useCallback(
		(editor: HTMLTextAreaElement) => {
			activeEditorRef.current = editor;
			if (activeLineFrameRef.current !== null) {
				return;
			}
			activeLineFrameRef.current = requestAnimationFrame(() => {
				activeLineFrameRef.current = null;
				const activeEditor = activeEditorRef.current;
				if (!activeEditor) {
					return;
				}
				updateEditorActiveLine(
					activeEditor,
					editorRef.current,
					highlightRef.current,
				);
			});
		},
		[],
	);

	return (
		<div ref={editorRef} className={className}>
			<div className="editor-active-line" aria-hidden="true" />
			<pre
				ref={highlightRef}
				aria-hidden="true"
				className={`editor-content ${highlightClassName}`}
			>
				{renderedLines}
			</pre>
			<textarea
				aria-label={ariaLabel}
				value={value}
				onChange={(event) => {
					onChange(event.target.value);
					scheduleActiveLineUpdate(event.currentTarget);
				}}
				onClick={(event) => scheduleActiveLineUpdate(event.currentTarget)}
				onBlur={clearActiveLine}
				onKeyDown={(event) => scheduleActiveLineUpdate(event.currentTarget)}
				onKeyUp={(event) => scheduleActiveLineUpdate(event.currentTarget)}
				onPointerDown={clearActiveLine}
				onScroll={(event) => {
					const editor = event.currentTarget;
					syncEditorScroll(editor, highlightRef.current);
					scheduleActiveLineUpdate(editor);
				}}
				onSelect={(event) => scheduleActiveLineUpdate(event.currentTarget)}
				spellCheck={false}
				wrap={wrap}
			/>
		</div>
	);
});

function editorLines(text: string): EditorLine[] {
	const matches = text.matchAll(/[^\r\n]*(?:\r\n|\r|\n|$)/g);
	const lines: EditorLine[] = [];
	for (const match of matches) {
		if (match.index === text.length && match[0] === "") {
			continue;
		}
		lines.push({
			key: `line-${match.index}`,
			text: match[0].replace(/\r\n|\r|\n$/, ""),
		});
	}
	return lines.length ? lines : [{ key: "line-0", text: "" }];
}

function updateEditorActiveLine(
	editor: HTMLTextAreaElement,
	container: HTMLDivElement | null,
	contentLayer: HTMLPreElement | null,
) {
	if (!container || !contentLayer) {
		return;
	}
	const lineNumber = editor.value
		.slice(0, editor.selectionStart)
		.split(/\r\n|\r|\n/).length;
	if (editor.wrap === "off") {
		updateUnwrappedEditorActiveLine(
			editor,
			container,
			contentLayer,
			lineNumber,
		);
		return;
	}
	const activeLine = contentLayer.children.item(lineNumber - 1);
	if (!(activeLine instanceof HTMLElement)) {
		delete container.dataset.activeLine;
		return;
	}
	const activeLineTop = activeLine.offsetTop - contentLayer.scrollTop;
	container.dataset.activeLine = "true";
	container.style.setProperty("--editor-active-line-top", `${activeLineTop}px`);
	container.style.setProperty(
		"--editor-active-line-height",
		`${activeLine.offsetHeight}px`,
	);
}

function updateUnwrappedEditorActiveLine(
	editor: HTMLTextAreaElement,
	container: HTMLDivElement,
	contentLayer: HTMLPreElement,
	lineNumber: number,
) {
	const editorStyle = getComputedStyle(editor);
	const measuredLineHeight =
		contentLayer.children.item(0)?.getBoundingClientRect().height || 0;
	const lineHeight =
		Number.parseFloat(editorStyle.lineHeight) ||
		measuredLineHeight ||
		FALLBACK_EDITOR_LINE_HEIGHT;
	const paddingTop = Number.parseFloat(editorStyle.paddingTop) || 0;

	container.dataset.activeLine = "true";
	container.style.setProperty(
		"--editor-active-line-top",
		`${paddingTop + (lineNumber - 1) * lineHeight - editor.scrollTop}px`,
	);
	container.style.setProperty("--editor-active-line-height", `${lineHeight}px`);
}

function syncEditorScroll(
	editor: HTMLTextAreaElement,
	contentLayer: HTMLPreElement | null,
) {
	if (!contentLayer) {
		return;
	}
	const maxContentScroll = Math.max(
		0,
		contentLayer.scrollHeight - contentLayer.clientHeight,
	);
	const nextScrollTop = Math.min(editor.scrollTop, maxContentScroll);
	if (editor.scrollTop !== nextScrollTop) {
		editor.scrollTop = nextScrollTop;
	}
	contentLayer.scrollTop = nextScrollTop;
	contentLayer.scrollLeft = editor.scrollLeft;
}
