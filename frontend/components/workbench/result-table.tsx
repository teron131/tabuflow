import {
	CompactSelection,
	DataEditor,
	type GridCell,
	GridCellKind,
	type GridColumn,
	GridColumnIcon,
	type GridSelection,
	type Item,
	type Rectangle,
	type Theme,
} from "@glideapps/glide-data-grid";
import { useCallback, useMemo, useState } from "react";
import type { SqlResult } from "@/lib/api";
import type { RoundingSettings } from "./types";

type ColumnKind = "boolean" | "number" | "uri" | "text";

type ColumnProfile = {
	name: string;
	kind: ColumnKind;
	width: number;
};

type SelectionStats = {
	label: string;
	numericCount: number;
	totalCells: number;
	sum: number;
	average: number;
	min: number;
	max: number;
};

const rawNumberMaximumFractionDigits = 20;

const emptyGridSelection: GridSelection = {
	columns: CompactSelection.empty(),
	rows: CompactSelection.empty(),
	current: undefined,
};

const gridTheme: Partial<Theme> = {
	accentColor: "#d59612",
	accentFg: "#171716",
	accentLight: "#fbf1d8",
	textDark: "#171716",
	textMedium: "#62605b",
	textLight: "#89857d",
	textHeader: "#171716",
	textHeaderSelected: "#171716",
	bgCell: "#fbfbf8",
	bgCellMedium: "#f5f4ef",
	bgHeader: "#e8e6df",
	bgHeaderHasFocus: "#f4dfab",
	bgHeaderHovered: "#f5f4ef",
	borderColor: "#c9c5bc",
	horizontalBorderColor: "#d7d3ca",
	headerBottomBorderColor: "#8d887e",
	cellHorizontalPadding: 9,
	cellVerticalPadding: 6,
	headerFontStyle: "600 11px",
	baseFontStyle: "12px",
	fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace',
	lineHeight: 1.35,
};

function isFiniteNumber(value: unknown): value is number {
	return typeof value === "number" && Number.isFinite(value);
}

function isBoolean(value: unknown): value is boolean {
	return typeof value === "boolean";
}

function isUri(value: unknown): value is string {
	if (typeof value !== "string") return false;
	return /^https?:\/\//i.test(value);
}

function toDisplayValue(value: unknown): string {
	if (value == null) return "";
	if (typeof value === "string") return value;
	if (typeof value === "number" || typeof value === "boolean") {
		return String(value);
	}
	try {
		return JSON.stringify(value) ?? String(value);
	} catch {
		return String(value);
	}
}

function toNumericValue(value: unknown): number | null {
	if (isFiniteNumber(value)) return value;
	if (typeof value !== "string") return null;
	const normalized = value.trim().replaceAll(",", "");
	if (
		!normalized ||
		!/^[+-]?(?:\d+\.?\d*|\.\d+)(?:e[+-]?\d+)?$/i.test(normalized)
	) {
		return null;
	}
	const parsed = Number(normalized);
	return Number.isFinite(parsed) ? parsed : null;
}

function formatNumberValue(value: number, rounding: RoundingSettings) {
	return new Intl.NumberFormat("en-US", {
		maximumFractionDigits: rounding.enabled
			? rounding.digits
			: rawNumberMaximumFractionDigits,
	}).format(value);
}

function inferColumnKind(
	column: string,
	rows: Array<Record<string, unknown>>,
): ColumnKind {
	const values = rows
		.map((row) => row[column])
		.filter((value) => value != null && value !== "");
	if (values.length > 0 && values.every(isFiniteNumber)) return "number";
	if (values.length > 0 && values.every(isBoolean)) return "boolean";
	if (values.length > 0 && values.every(isUri)) return "uri";
	return "text";
}

function columnIcon(kind: ColumnKind) {
	if (kind === "number") return GridColumnIcon.HeaderNumber;
	if (kind === "boolean") return GridColumnIcon.HeaderBoolean;
	if (kind === "uri") return GridColumnIcon.HeaderUri;
	return GridColumnIcon.HeaderString;
}

function columnWidth(
	column: string,
	rows: Array<Record<string, unknown>>,
	kind: ColumnKind,
) {
	const sampleWidth = rows.slice(0, 30).reduce((width, row) => {
		return Math.max(width, toDisplayValue(row[column]).length);
	}, column.length);
	if (kind === "boolean") return Math.max(118, column.length * 8 + 42);
	if (kind === "number")
		return Math.min(220, Math.max(132, sampleWidth * 8 + 42));
	return Math.min(360, Math.max(132, sampleWidth * 8 + 42));
}

function columnProfiles(
	columns: string[],
	rows: Array<Record<string, unknown>>,
): ColumnProfile[] {
	return columns.map((name) => {
		const kind = inferColumnKind(name, rows);
		return {
			name,
			kind,
			width: columnWidth(name, rows, kind),
		};
	});
}

function selectedRectangles(selection: GridSelection): Rectangle[] {
	const current = selection.current;
	if (!current) return [];
	const rectangles = [...current.rangeStack, current.range];
	return rectangles.filter((range) => range.width > 0 && range.height > 0);
}

function selectionLabel(rectangles: Rectangle[], totalCells: number) {
	const activeRange = rectangles.at(-1) ?? rectangles[0];
	if (rectangles.length === 1) {
		return `${activeRange.height.toLocaleString()} x ${activeRange.width.toLocaleString()}`;
	}
	return `${totalCells.toLocaleString()} cells`;
}

function selectionStats(
	selection: GridSelection,
	columns: string[],
	rows: Array<Record<string, unknown>>,
): SelectionStats | null {
	const rectangles = selectedRectangles(selection);
	if (!rectangles.length) return null;

	let totalCells = 0;
	let numericCount = 0;
	let sum = 0;
	let min = Number.POSITIVE_INFINITY;
	let max = Number.NEGATIVE_INFINITY;

	for (const range of rectangles) {
		const endRow = Math.min(rows.length, range.y + range.height);
		const endColumn = Math.min(columns.length, range.x + range.width);
		for (let rowIndex = range.y; rowIndex < endRow; rowIndex += 1) {
			const row = rows[rowIndex];
			for (
				let columnIndex = range.x;
				columnIndex < endColumn;
				columnIndex += 1
			) {
				totalCells += 1;
				const value = toNumericValue(row?.[columns[columnIndex]]);
				if (value == null) continue;
				numericCount += 1;
				sum += value;
				min = Math.min(min, value);
				max = Math.max(max, value);
			}
		}
	}

	if (!totalCells) return null;
	return {
		label: selectionLabel(rectangles, totalCells),
		numericCount,
		totalCells,
		sum,
		average: numericCount ? sum / numericCount : 0,
		min,
		max,
	};
}

export function ResultTable({
	result,
	rounding,
}: {
	result: SqlResult | null;
	rounding: RoundingSettings;
}) {
	if (!result) {
		return (
			<div className="empty-state">Run SQL to populate the result grid.</div>
		);
	}
	if (result.status === "error") {
		return (
			<div className="error-state">{result.message || result.error_type}</div>
		);
	}
	const columns = result.columns || [];
	const rows = result.rows || [];
	if (!columns.length) {
		return (
			<div className="empty-state">{result.summary || "Query completed."}</div>
		);
	}
	return (
		<ResultGrid
			columns={columns}
			resultRowCount={result.row_count}
			rounding={rounding}
			rows={rows}
			truncated={result.truncated}
		/>
	);
}

function ResultGrid({
	columns,
	resultRowCount,
	rounding,
	rows,
	truncated,
}: {
	columns: string[];
	resultRowCount?: number;
	rounding: RoundingSettings;
	rows: Array<Record<string, unknown>>;
	truncated?: boolean;
}) {
	const profiles = useMemo(
		() => columnProfiles(columns, rows),
		[columns, rows],
	);
	const [gridSelection, setGridSelection] =
		useState<GridSelection>(emptyGridSelection);
	const stats = useMemo(
		() => selectionStats(gridSelection, columns, rows),
		[columns, gridSelection, rows],
	);
	const gridColumns = useMemo<readonly GridColumn[]>(
		() =>
			profiles.map((profile) => ({
				id: profile.name,
				title: profile.name,
				icon: columnIcon(profile.kind),
				width: profile.width,
				grow: 1,
				hasMenu: false,
			})),
		[profiles],
	);
	const getCellContent = useCallback(
		([col, row]: Item): GridCell => {
			const profile = profiles[col];
			const column = profile?.name;
			const value = rows[row]?.[column];
			if (profile?.kind === "number" && isFiniteNumber(value)) {
				const displayData = formatNumberValue(value, rounding);
				return {
					kind: GridCellKind.Number,
					allowOverlay: false,
					readonly: true,
					data: value,
					displayData,
					contentAlign: "right",
					copyData: displayData,
				};
			}
			if (profile?.kind === "boolean" && isBoolean(value)) {
				return {
					kind: GridCellKind.Boolean,
					allowOverlay: false,
					readonly: true,
					data: value,
					contentAlign: "center",
					copyData: String(value),
				};
			}
			if (profile?.kind === "uri" && isUri(value)) {
				return {
					kind: GridCellKind.Uri,
					allowOverlay: false,
					readonly: true,
					data: value,
					displayData: value,
					hoverEffect: true,
					copyData: value,
				};
			}
			const displayData = toDisplayValue(value);
			return {
				kind: GridCellKind.Text,
				allowOverlay: false,
				readonly: true,
				data: displayData,
				displayData,
				copyData: displayData,
			};
		},
		[profiles, rounding, rows],
	);
	const visibleRows = resultRowCount ?? rows.length;
	const statusText = `${visibleRows.toLocaleString()} row${visibleRows === 1 ? "" : "s"} x ${columns.length.toLocaleString()} column${columns.length === 1 ? "" : "s"}`;

	return (
		<div className="result-grid-shell" data-testid="result-grid">
			<div className="result-grid-frame">
				<DataEditor
					className="result-data-grid"
					columns={gridColumns}
					copyHeaders
					freezeColumns={0}
					getCellContent={getCellContent}
					getCellsForSelection
					gridSelection={gridSelection}
					headerHeight={34}
					height="100%"
					maxColumnWidth={420}
					minColumnWidth={96}
					onPaste={false}
					onGridSelectionChange={setGridSelection}
					rangeSelect="multi-rect"
					rowHeight={30}
					rowMarkers="clickable-number"
					rowSelect="multi"
					rows={rows.length}
					smoothScrollX
					theme={gridTheme}
					verticalBorder
					width="100%"
				/>
			</div>
			<footer className="result-grid-status">
				<span className="result-status-main">{statusText}</span>
				<div className="selection-stats" aria-live="polite">
					{stats ? (
						<span className="selection-stat selection-shape">
							{stats.label}
						</span>
					) : null}
					{stats && stats.numericCount > 0 ? (
						<>
							<span className="selection-stat">
								sum {formatNumberValue(stats.sum, rounding)}
							</span>
							<span className="selection-stat">
								avg {formatNumberValue(stats.average, rounding)}
							</span>
							<span className="selection-stat">
								min {formatNumberValue(stats.min, rounding)}
							</span>
							<span className="selection-stat">
								max {formatNumberValue(stats.max, rounding)}
							</span>
							{stats.numericCount < stats.totalCells ? (
								<span className="selection-stat">
									nums {stats.numericCount.toLocaleString()}
								</span>
							) : null}
						</>
					) : null}
					{truncated ? <span className="selection-stat">truncated</span> : null}
				</div>
			</footer>
		</div>
	);
}
