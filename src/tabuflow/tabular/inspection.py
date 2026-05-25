"""Tabular inspect command implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .hints import structure_hints
from .ingestion import (
    MAX_SAMPLE_ROWS,
    load_rows,
    stream_csv_window,
    tabular_dimensions,
    tabular_summary_from_counts,
)
from .profiling import profile_tabular_file
from .segmentation import compute_region_boxes, header_candidates, profile_region_boxes


def inspect_tabular_file(
    path: str | Path,
    *,
    start_row: int = 1,
    limit: int = 5,
    start_col: int = 1,
    end_col: int | None = None,
    sheet: str | None = None,
) -> dict[str, Any]:
    """Return a bounded raw grid window from a tabular file."""
    path = Path(path)
    safe_start = max(1, start_row)
    safe_limit = max(1, limit)
    safe_start_col = max(1, start_col)
    if path.suffix.lower() == ".csv":
        selected_rows, summary = stream_csv_window(
            path,
            start_row=safe_start,
            limit=safe_limit,
            start_col=safe_start_col,
            end_col=end_col,
        )
        profile = profile_tabular_file(
            path,
            max_sample_rows=MAX_SAMPLE_ROWS,
            sheet=sheet,
        )
        summary_payload = tabular_summary_from_counts(
            row_count=None,
            column_count=None,
            format_info={
                "format": summary["format"],
                "encoding": summary["encoding"],
                "delimiter": summary["delimiter"],
                "quotechar": summary["quotechar"],
                "sheet_names": [],
                "read_mode": summary["read_mode"],
            },
        )
        header_candidate_rows = profile.get("header_candidates", [])
        regions = profile.get("regions", [])
    else:
        rows, format_info = load_rows(path, sheet=sheet)
        row_count, column_count = tabular_dimensions(rows)
        safe_end_col = column_count if end_col is None else max(safe_start_col, end_col)
        end_row = safe_start + safe_limit - 1
        selected_source_rows = rows[safe_start - 1 : end_row]
        selected_rows = [row[safe_start_col - 1 : safe_end_col] for row in selected_source_rows]
        summary_payload = tabular_summary_from_counts(
            row_count=row_count,
            column_count=column_count,
            format_info={
                **format_info,
                "read_mode": "full_layout",
            },
        )
        region_boxes = compute_region_boxes(rows)
        header_candidate_rows = header_candidates(rows, region_boxes=region_boxes)
        regions = profile_region_boxes(region_boxes)

    preview_row_count, preview_column_count = tabular_dimensions(selected_rows)

    return {
        "path": str(path),
        **summary_payload,
        "preview_row_count": preview_row_count,
        "preview_column_count": preview_column_count,
        "start_row": safe_start,
        "end_row": safe_start + preview_row_count - 1 if selected_rows else safe_start - 1,
        "start_col": safe_start_col,
        "end_col": safe_start_col + preview_column_count - 1 if selected_rows else safe_start_col - 1,
        "structure_hints": structure_hints(
            header_candidate_rows=header_candidate_rows,
            regions=regions,
            sheet_names=summary_payload.get("sheet_names", []),
        ),
        "header_candidates": header_candidate_rows,
        "regions": regions,
        "rows": selected_rows,
    }
