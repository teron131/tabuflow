"""PyMuPDF-detected table extraction."""

from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Any

import pymupdf

from .pages import page_numbers
from .table_records import (
    clean_extracted_table,
    extend_rows_merging_first_column_continuations,
    records_from_detected_table,
    records_from_forced_columns,
)


def pymupdf_table_outputs(
    pdf_path: Path,
    config: dict[str, Any],
) -> list[dict[str, Any]]:
    """Extract each PyMuPDF-detected table as a separate output."""
    outputs: list[dict[str, Any]] = []
    merge_tables = str(config.get("merge_tables", "auto"))
    min_rows = int(config.get("min_rows", 1))
    forced_columns = [str(column) for column in config.get("output_columns", [])]
    min_filled_cells = int(config.get("min_filled_cells", 1))
    table_detection_options = find_tables_kwargs(config)
    with pymupdf.open(str(pdf_path)) as document:
        for page_number in page_numbers(document, config):
            page = document[page_number - 1]
            with contextlib.redirect_stdout(io.StringIO()):
                tables = page.find_tables(**table_detection_options)
            for source_table_number, table in enumerate(tables.tables, start=1):
                extracted_rows, header_names = clean_extracted_table(table.extract(), table.header.names)
                if len(extracted_rows) < min_rows:
                    continue
                if forced_columns:
                    columns, rows = records_from_forced_columns(extracted_rows, forced_columns, min_filled_cells)
                else:
                    columns, rows = records_from_detected_table(extracted_rows, header_names)
                if config.get("require_header") and all(column.startswith("column_") and column[7:].isdigit() for column in columns):
                    continue
                if not rows:
                    continue
                outputs.append(
                    {
                        "mode": "pymupdf_tables",
                        "source_page": page_number,
                        "source_table": source_table_number,
                        "source_bbox": list(table.bbox),
                        "source_page_height": float(page.rect.height),
                        "columns": columns,
                        "rows": rows,
                        "merge_first_column_continuations": bool(forced_columns),
                    }
                )
    return merge_consecutive_table_outputs(outputs, merge_tables=merge_tables)


def find_tables_kwargs(config: dict[str, Any]) -> dict[str, Any]:
    """Return PyMuPDF table-detection options from a detected-table config."""
    kwargs: dict[str, Any] = {}
    for key in ("vertical_strategy", "horizontal_strategy"):
        if value := config.get(key):
            kwargs[key] = str(value).replace("-", "_")
    if clip := config.get("clip"):
        if len(clip) != 4:
            raise ValueError("PDF table clip must contain exactly four values: X0,Y0,X1,Y1.")
        kwargs["clip"] = pymupdf.Rect(*(float(value) for value in clip))
    return kwargs


def merge_consecutive_table_outputs(
    outputs: list[dict[str, Any]],
    *,
    merge_tables: str = "auto",
) -> list[dict[str, Any]]:
    """Merge adjacent detected tables that repeat the same schema."""
    if merge_tables not in {"auto", "always", "never"}:
        raise ValueError(f"Unsupported detected-table merge policy: {merge_tables}")
    merged_outputs: list[dict[str, Any]] = []
    for output in outputs:
        if merged_outputs and should_merge_table_outputs(merged_outputs[-1], output, merge_tables=merge_tables):
            if output.get("merge_first_column_continuations"):
                extend_rows_merging_first_column_continuations(merged_outputs[-1]["rows"], output["rows"], output["columns"])
            else:
                merged_outputs[-1]["rows"].extend(output["rows"])
            merged_outputs[-1]["source_pages"].append(output["source_page"])
            merged_outputs[-1]["source_tables"].append(output["source_table"])
            merged_outputs[-1]["source_bboxes"].append(output.get("source_bbox"))
            merged_outputs[-1]["last_source_page"] = output["source_page"]
            merged_outputs[-1]["last_source_bbox"] = output.get("source_bbox")
            merged_outputs[-1]["last_source_page_height"] = output.get("source_page_height")
            continue
        merged_outputs.append(
            {
                **output,
                "source_pages": [output["source_page"]],
                "source_tables": [output["source_table"]],
                "source_bboxes": [output.get("source_bbox")],
                "last_source_page": output["source_page"],
                "last_source_bbox": output.get("source_bbox"),
                "last_source_page_height": output.get("source_page_height"),
            }
        )
    return merged_outputs


def should_merge_table_outputs(
    previous: dict[str, Any],
    current: dict[str, Any],
    *,
    merge_tables: str,
) -> bool:
    """Return whether two detected table chunks look like one continued table."""
    if current["columns"] != previous["columns"]:
        return False
    if merge_tables == "never":
        return False
    if merge_tables == "always":
        return True
    if not previous.get("source_bbox") or not current.get("source_bbox"):
        return True
    previous_page = int(previous.get("last_source_page", previous["source_page"]))
    previous_bbox = previous.get("last_source_bbox", previous["source_bbox"])
    previous_page_height = previous.get("last_source_page_height", previous.get("source_page_height"))
    previous_chunk = {
        **previous,
        "source_page": previous_page,
        "source_bbox": previous_bbox,
        "source_page_height": previous_page_height,
    }
    if current["source_page"] == previous_page:
        previous_bottom = float(previous_chunk["source_bbox"][3])
        current_top = float(current["source_bbox"][1])
        return 0 <= current_top - previous_bottom <= 18
    if current["source_page"] != previous_page + 1:
        return False
    return page_break_tables_touch(previous_chunk, current)


def page_break_tables_touch(
    previous: dict[str, Any],
    current: dict[str, Any],
) -> bool:
    """Return whether adjacent-page tables straddle a page break."""
    page_height = float(previous.get("source_page_height") or current.get("source_page_height") or 0)
    if page_height <= 0:
        return False
    previous_bottom = float(previous["source_bbox"][3])
    current_top = float(current["source_bbox"][1])
    return previous_bottom >= page_height * 0.75 and current_top <= page_height * 0.25
