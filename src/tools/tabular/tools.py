"""Standalone tabular inspection and extraction tools."""

from __future__ import annotations

from pathlib import Path
import statistics
from typing import Any

from .ingestion import (
    MAX_FULL_EXTRACT_BYTES,
    MAX_FULL_PROFILE_BYTES,
    MAX_METADATA_ROWS,
    MAX_SAMPLE_ROWS,
    count_non_empty,
    is_blank,
    load_rows,
    preview_dimensions,
    stream_csv_profile,
    stream_csv_window,
    tabular_dimensions,
    tabular_summary,
    tabular_summary_from_counts,
)
from .segmentation import compute_region_boxes, header_candidates, profile_region_boxes, segment_tabular_blocks
from .storage import fingerprint, fingerprint_from_samples, load_tables_into_sqlite, resolve_root_dir

FOOTER_LIKE_LABELS = {"total", "grand total", "rounding error"}
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
}


def _structure_hints(
    *,
    header_candidate_rows: list[dict[str, Any]],
    regions: list[dict[str, Any]],
    sheet_names: list[str] | None = None,
) -> dict[str, Any]:
    """Build compact hints for agents before they inspect raw rows manually."""
    stable_candidates = [candidate for candidate in header_candidate_rows if not candidate.get("has_stronger_header_ahead")]
    header_pool = stable_candidates or header_candidate_rows
    best_header = max(header_pool, key=lambda candidate: (candidate.get("non_empty_cells", 0), -candidate.get("row", 0))) if header_pool else None
    suggested_start_row = best_header["row"] if best_header else None
    return {
        "likely_header_row": suggested_start_row,
        "suggested_data_start_row": suggested_start_row + 1 if suggested_start_row else None,
        "header_candidate_count": len(header_candidate_rows),
        "region_count": len(regions),
        "sheet_names": sheet_names or [],
    }


def _footer_like_row_hints(
    tables: list[dict[str, Any]],
    metadata: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Report metadata rows after tables that look like totals or rounding footers."""
    if not tables:
        return []
    last_table_end = max(int(table["row_end"]) for table in tables)
    hints: list[dict[str, Any]] = []
    for block in metadata:
        if int(block["row_start"]) <= last_table_end:
            continue
        for offset, row in enumerate(block.get("rows", [])):
            non_empty = [cell.strip() for cell in row if cell.strip()]
            labels = {cell.lower() for cell in non_empty}
            if labels.intersection(FOOTER_LIKE_LABELS):
                hints.append(
                    {
                        "row": int(block["row_start"]) + offset,
                        "values": row,
                        "reason": "footer_like_label",
                    }
                )
    return hints


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

    preview_row_count, preview_column_count = preview_dimensions(selected_rows)

    return {
        "path": str(path),
        **summary_payload,
        "preview_row_count": preview_row_count,
        "preview_column_count": preview_column_count,
        "start_row": safe_start,
        "end_row": safe_start + preview_row_count - 1 if selected_rows else safe_start - 1,
        "start_col": safe_start_col,
        "end_col": safe_start_col + preview_column_count - 1 if selected_rows else safe_start_col - 1,
        "structure_hints": _structure_hints(
            header_candidate_rows=header_candidate_rows,
            regions=regions,
            sheet_names=summary_payload.get("sheet_names", []),
        ),
        "header_candidates": header_candidate_rows,
        "regions": regions,
        "rows": selected_rows,
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
        summary = stream_csv_profile(path, max_sample_rows=max_sample_rows)
        profile_fingerprint = fingerprint_from_samples(
            row_count=summary["row_count"],
            column_count=summary["column_count"],
            top_rows=summary["top_rows"],
            bottom_rows=summary["bottom_rows"],
            header_candidates=[],
            max_sample_rows=max_sample_rows,
        )
        header_candidate_rows: list[dict[str, Any]] = []
        regions: list[dict[str, Any]] = []
        return {
            "path": str(path),
            **{key: value for key, value in summary.items() if key not in {"top_rows", "bottom_rows"}},
            "fingerprint": profile_fingerprint,
            "structure_hints": _structure_hints(
                header_candidate_rows=header_candidate_rows,
                regions=regions,
                sheet_names=summary.get("sheet_names", []),
            ),
        }

    rows, format_info = load_rows(path, sheet=sheet)
    region_boxes = compute_region_boxes(rows)
    detected_header_candidates = header_candidates(rows, region_boxes=region_boxes)
    non_empty_counts = [count_non_empty(row) for row in rows if not is_blank(row)]

    regions = profile_region_boxes(region_boxes)
    return {
        "path": str(path),
        **tabular_summary(rows, format_info),
        "fingerprint": fingerprint(
            rows,
            max_sample_rows=max_sample_rows,
            header_candidates=detected_header_candidates,
        ),
        "non_empty_row_count": len(non_empty_counts),
        "blank_row_count": len(rows) - len(non_empty_counts),
        "max_non_empty_cells_in_row": max(non_empty_counts, default=0),
        "median_non_empty_cells_per_non_blank_row": statistics.median(non_empty_counts) if non_empty_counts else 0,
        "sample_rows": rows[:max_sample_rows],
        "structure_hints": _structure_hints(
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
    summary = inspect_tabular_file(path, limit=1)
    sheet_names = summary.get("sheet_names", [])
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
        "format": summary["format"],
        "sheet_names": sheet_names,
        "sheet_count": len(sheet_names),
        "sheets": sheet_profiles,
    }


def _recover_tabular_blocks(
    path: Path,
    *,
    sample_rows: int = MAX_SAMPLE_ROWS,
    metadata_rows: int = MAX_METADATA_ROWS,
    sheet: str | None = None,
) -> dict[str, Any]:
    """Recover metadata and table blocks for future extraction backends."""
    rows, format_info = load_rows(path, sheet=sheet)
    metadata, tables = segment_tabular_blocks(
        rows,
        table_sample_rows=sample_rows,
        metadata_sample_rows=metadata_rows,
    )

    return {
        "path": str(path),
        **tabular_summary(rows, format_info),
        "metadata": metadata,
        "tables": tables,
    }


def extract_tabular_file(
    path: str | Path,
    *,
    root_dir: str | Path | None = None,
    sample_rows: int = MAX_SAMPLE_ROWS,
    metadata_rows: int = MAX_METADATA_ROWS,
    sheet: str | None = None,
) -> dict[str, Any]:
    """Extract tables and load them into the shared SQLite cache."""
    path = Path(path)
    if not path.is_absolute() and root_dir is not None:
        path = resolve_root_dir(root_dir=root_dir) / path
    if path.suffix.lower() == ".csv" and path.stat().st_size > MAX_FULL_EXTRACT_BYTES:
        raise ValueError(f"CSV extraction currently requires a full in-memory layout pass and is capped at {MAX_FULL_EXTRACT_BYTES} bytes for safety: {path}")

    profile = profile_tabular_file(path, max_sample_rows=sample_rows, sheet=sheet)
    recovered = _recover_tabular_blocks(
        path,
        sample_rows=None,
        metadata_rows=metadata_rows,
        sheet=sheet,
    )
    if not recovered["tables"]:
        return {
            "path": recovered["path"],
            "format": recovered["format"],
            "sheet_name": recovered.get("sheet_name"),
            "status": "empty",
            "artifact_backend": "sqlite",
            "database_path": "",
            "fingerprint": profile["fingerprint"],
            "recovered_table_count": 0,
            "recovered_metadata_block_count": len(recovered["metadata"]),
            "excluded_row_hints": _footer_like_row_hints(recovered["tables"], recovered["metadata"]),
            "tables": [],
            "message": "Tabular extraction completed but did not recover importable tables.",
        }

    loaded = load_tables_into_sqlite(
        recovered,
        root_dir=root_dir,
        fingerprint=profile["fingerprint"],
    )

    return {
        "path": recovered["path"],
        "format": recovered["format"],
        "sheet_name": recovered.get("sheet_name"),
        "status": "loaded",
        "artifact_backend": "sqlite",
        "database_path": loaded["database_path"],
        "fingerprint": profile["fingerprint"],
        "recovered_table_count": len(recovered["tables"]),
        "recovered_metadata_block_count": len(recovered["metadata"]),
        "excluded_row_hints": _footer_like_row_hints(recovered["tables"], recovered["metadata"]),
        "tables": loaded["tables"],
    }
