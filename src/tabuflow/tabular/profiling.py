"""Tabular profile command implementation."""

from __future__ import annotations

from pathlib import Path
import statistics
from typing import Any

from .hints import structure_hints
from .ingestion import (
    MAX_FULL_PROFILE_BYTES,
    MAX_SAMPLE_ROWS,
    TabularReader,
    load_rows,
    tabular_summary,
    workbook_sheet_names,
)
from .segmentation import compute_region_boxes, header_candidates, profile_region_boxes

WORKBOOK_SHEET_PROFILE_FIELDS = {
    "row_count",
    "column_count",
    "non_empty_row_count",
    "blank_row_count",
    "max_non_empty_cells_in_row",
    "median_non_empty_cells_per_non_blank_row",
    "structure_hints",
    "header_candidates",
    "regions",
    "sample_rows",
}


def profile_tabular_file(
    path: str | Path,
    *,
    max_sample_rows: int = MAX_SAMPLE_ROWS,
    sheet: str | None = None,
) -> dict[str, Any]:
    """Profile a tabular file with read-only structural hints."""
    path = Path(path)
    if path.suffix.lower() == ".csv" and path.stat().st_size > MAX_FULL_PROFILE_BYTES:
        summary = TabularReader.from_path(path, sheet=sheet).streaming_profile(max_sample_rows=max_sample_rows)
        header_candidate_rows: list[dict[str, Any]] = []
        regions: list[dict[str, Any]] = []
        return {
            "path": str(path),
            **summary,
            "structure_hints": structure_hints(
                header_candidate_rows=header_candidate_rows,
                regions=regions,
                sheet_names=summary.get("sheet_names", []),
            ),
        }

    rows, format_info = load_rows(path, sheet=sheet)
    region_boxes = compute_region_boxes(rows)
    detected_header_candidates = header_candidates(rows, region_boxes=region_boxes)
    non_empty_counts = [non_empty_count for row in rows if (non_empty_count := sum(bool(cell.strip()) for cell in row)) > 0]

    regions = profile_region_boxes(region_boxes)
    return {
        "path": str(path),
        **tabular_summary(rows, format_info),
        "non_empty_row_count": len(non_empty_counts),
        "blank_row_count": len(rows) - len(non_empty_counts),
        "max_non_empty_cells_in_row": max(non_empty_counts, default=0),
        "median_non_empty_cells_per_non_blank_row": statistics.median(non_empty_counts) if non_empty_counts else 0,
        "sample_rows": rows[:max_sample_rows],
        "structure_hints": structure_hints(
            header_candidate_rows=detected_header_candidates,
            regions=regions,
            sheet_names=format_info.get("sheet_names", []),
        ),
        "header_candidates": detected_header_candidates,
        "regions": regions,
    }


def profile_tabular_workbook_sheets(
    path: str | Path,
    *,
    max_sample_rows: int = MAX_SAMPLE_ROWS,
) -> dict[str, Any]:
    """Profile all workbook sheets with compact structural hints."""
    path = Path(path)
    sheet_names = workbook_sheet_names(path)
    if not sheet_names:
        raise ValueError(f"All-sheet profiling requires an XLS or XLSX workbook: {path}")
    sheet_profiles = []
    for sheet_name in sheet_names:
        profile = profile_tabular_file(path, max_sample_rows=max_sample_rows, sheet=sheet_name)
        sheet_profiles.append(
            {
                "sheet_name": sheet_name,
                **{key: value for key, value in profile.items() if key in WORKBOOK_SHEET_PROFILE_FIELDS},
            }
        )
    return {
        "path": str(path),
        "format": path.suffix.lower().removeprefix("."),
        "sheet_names": sheet_names,
        "sheet_count": len(sheet_names),
        "sheets": sheet_profiles,
    }
