"""Coordinate-based PDF table extraction."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf

from .pages import page_numbers
from .table_records import clean_cell


def visual_lines(
    page: pymupdf.Page,
    *,
    y_tolerance: float,
) -> list[tuple[float, list[tuple[float, str]]]]:
    """Group PyMuPDF word records into visual rows."""
    rows: list[tuple[float, list[tuple[float, str]]]] = []
    for word in sorted(page.get_text("words"), key=lambda item: (round(float(item[1]) / y_tolerance) * y_tolerance, float(item[0]))):
        x0, y0, _x1, _y1, text, *_rest = word
        if not rows or abs(rows[-1][0] - float(y0)) > y_tolerance:
            rows.append((float(y0), []))
        rows[-1][1].append((float(x0), str(text)))
    return rows


def column_value(
    parts: list[tuple[float, str]],
    column: dict[str, Any],
) -> str:
    """Return the joined words that fall inside one configured x-band."""
    x_min = float(column["x_min"])
    x_max = float(column["x_max"])
    return clean_cell(" ".join(text for x, text in parts if x_min <= x < x_max))


def coordinate_rows(
    pdf_path: Path,
    config: dict[str, Any],
) -> list[dict[str, str]]:
    """Extract visual rows from configured x-column bands."""
    columns = list(config["columns"])
    y_tolerance = float(config.get("y_tolerance", 4))
    y_min = float(config.get("y_min", 0))
    y_max = float(config.get("y_max", 10_000))
    required_columns = [str(column) for column in config.get("required_columns", [])]
    continuation_column = str(config["continuation_column"]) if config.get("continuation_column") else None
    if continuation_column:
        anchor_columns = [column for column in required_columns if column != continuation_column]
        if anchor_columns:
            return coordinate_anchor_rows(
                pdf_path,
                config,
                columns=columns,
                y_tolerance=y_tolerance,
                y_min=y_min,
                y_max=y_max,
                required_columns=required_columns,
                anchor_columns=anchor_columns,
                continuation_column=continuation_column,
            )

    rows: list[dict[str, str]] = []

    with pymupdf.open(str(pdf_path)) as document:
        for page_number in page_numbers(document, config):
            page = document[page_number - 1]
            for y, parts in visual_lines(page, y_tolerance=y_tolerance):
                if not y_min <= y <= y_max:
                    continue
                row = {str(column["name"]): column_value(parts, column) for column in columns}
                if config.get("include_page"):
                    row["page"] = str(page_number)
                if row_matches_skip_filters(row, config):
                    continue
                if required_columns and not all(row.get(column_name) for column_name in required_columns):
                    continue
                if any(row.get(str(column["name"])) for column in columns):
                    rows.append(row)
    return rows


def coordinate_anchor_rows(
    pdf_path: Path,
    config: dict[str, Any],
    *,
    columns: list[dict[str, Any]],
    y_tolerance: float,
    y_min: float,
    y_max: float,
    required_columns: list[str],
    anchor_columns: list[str],
    continuation_column: str,
) -> list[dict[str, str]]:
    """Extract rows whose stable columns anchor nearby wrapped text."""
    anchor_y_slop = float(config.get("anchor_y_slop", y_tolerance * 2))
    rows: list[dict[str, str]] = []

    with pymupdf.open(str(pdf_path)) as document:
        for page_number in page_numbers(document, config):
            page_lines = []
            for y, parts in visual_lines(document[page_number - 1], y_tolerance=y_tolerance):
                if not y_min <= y <= y_max:
                    continue
                row = {str(column["name"]): column_value(parts, column) for column in columns}
                page_lines.append(
                    {
                        "y": y,
                        "row": row,
                    }
                )

            anchors = [line for line in page_lines if all(line["row"].get(column) for column in anchor_columns)]
            for anchor_index, anchor in enumerate(anchors):
                has_next_anchor = anchor_index < len(anchors) - 1
                next_anchor_y = anchors[anchor_index + 1]["y"] if has_next_anchor else y_max
                band_start = max(y_min, anchor["y"] - anchor_y_slop)
                band_end = min(y_max, next_anchor_y - anchor_y_slop if has_next_anchor else y_max)
                wrapped_values = [line["row"][continuation_column] for line in page_lines if band_start <= line["y"] < band_end and line["row"].get(continuation_column)]
                row = dict(anchor["row"])
                row[continuation_column] = clean_cell(" ".join(wrapped_values))
                if config.get("include_page"):
                    row["page"] = str(page_number)
                if row_matches_skip_filters(row, config):
                    continue
                if required_columns and not all(row.get(column) for column in required_columns):
                    continue
                rows.append(row)
    return rows


def row_matches_skip_filters(
    row: dict[str, str],
    config: dict[str, Any],
) -> bool:
    """Return whether a visual row should be dropped by text cleanup filters."""
    skip_lines = set(config.get("skip_lines", []))
    skip_prefixes = list(config.get("skip_prefixes", []))
    return any(value in skip_lines or any(value.startswith(prefix) for prefix in skip_prefixes) for value in row.values())
