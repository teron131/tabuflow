"""Tabular extract command implementation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .ingestion import MAX_FULL_EXTRACT_BYTES, MAX_METADATA_ROWS, MAX_SAMPLE_ROWS, load_rows, tabular_summary
from .segmentation import segment_tabular_blocks
from .storage import load_tables_into_sqlite, resolve_root_dir

FOOTER_LIKE_LABELS = {"total", "grand total", "rounding error"}


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
    metadata_rows: int = MAX_METADATA_ROWS,
    sheet: str | None = None,
) -> dict[str, Any]:
    """Extract tables and load them into the shared SQLite cache."""
    path = Path(path)
    if not path.is_absolute() and root_dir is not None:
        path = resolve_root_dir(root_dir=root_dir) / path
    if path.suffix.lower() == ".csv" and path.stat().st_size > MAX_FULL_EXTRACT_BYTES:
        raise ValueError(f"CSV extraction currently requires a full in-memory layout pass and is capped at {MAX_FULL_EXTRACT_BYTES} bytes for safety: {path}")

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
            "recovered_table_count": 0,
            "recovered_metadata_block_count": len(recovered["metadata"]),
            "excluded_row_hints": _footer_like_row_hints(recovered["tables"], recovered["metadata"]),
            "tables": [],
            "message": "Tabular extraction completed but did not recover importable tables.",
        }

    loaded = load_tables_into_sqlite(
        recovered,
        root_dir=root_dir,
    )
    table_fingerprints = [table["fingerprint"] for table in loaded["tables"]]

    return {
        "path": recovered["path"],
        "format": recovered["format"],
        "sheet_name": recovered.get("sheet_name"),
        "status": "loaded",
        "artifact_backend": "sqlite",
        "database_path": loaded["database_path"],
        "fingerprints": table_fingerprints,
        "recovered_table_count": len(recovered["tables"]),
        "recovered_metadata_block_count": len(recovered["metadata"]),
        "excluded_row_hints": _footer_like_row_hints(recovered["tables"], recovered["metadata"]),
        "tables": loaded["tables"],
    }
