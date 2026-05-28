"""Tabular extract command implementation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..pdf.common import PDF_TABLES_MANIFEST_NAME
from ..workspace_db import resolve_root_dir
from .formulas import formulas_with_table_refs, workbook_formula_cells
from .ingestion import MAX_FULL_EXTRACT_BYTES, MAX_METADATA_ROWS, load_rows, tabular_summary, workbook_sheet_names
from .segmentation import segment_tabular_blocks
from .storage import load_tables_into_sqlite

FOOTER_LIKE_LABELS = {"total", "grand total", "rounding error"}
PDF_TABLE_SOURCE_METADATA_KEYS = (
    "document_order",
    "name",
    "page_tag",
    "source_page",
    "source_pages",
    "source_tables",
    "source_bboxes",
    "split_value",
    "split_values",
    "merge_evidence",
    "table_end_reasons",
)


def _workspace_relative_path(path: Path, root_dir: str | Path | None) -> str:
    """Return a root-relative path for metadata when possible."""
    if root_dir is None:
        return str(path)
    resolved_root = resolve_root_dir(root_dir=root_dir)
    try:
        return str(path.resolve().relative_to(resolved_root))
    except ValueError:
        return str(path)


def _pdf_table_manifest_path(path: Path) -> Path | None:
    """Return the sibling PDF table manifest for an extracted table CSV."""
    if path.parent.name != "tables" or path.parent.parent.name != "work":
        return None
    manifest_path = path.parent.parent / PDF_TABLES_MANIFEST_NAME
    return manifest_path if manifest_path.is_file() else None


def _manifest_table_matches_path(
    table: dict[str, Any],
    *,
    csv_path: Path,
    tables_dir: Path,
) -> bool:
    """Return whether one manifest entry points at the CSV being imported."""
    path_text = str(table.get("path") or "")
    if not path_text:
        return False
    manifest_path = Path(path_text)
    candidates = [manifest_path]
    if not manifest_path.is_absolute():
        candidates.append(tables_dir / manifest_path.name)
    return any(candidate.resolve() == csv_path.resolve() for candidate in candidates if candidate.name) or manifest_path.name == csv_path.name


def _pdf_table_source_metadata(
    path: Path,
    *,
    root_dir: str | Path | None,
) -> dict[str, Any]:
    """Return source metadata for a reviewed PDF table CSV, when available."""
    manifest_path = _pdf_table_manifest_path(path)
    if manifest_path is None:
        return {}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    tables = [table for table in manifest.get("tables", []) if isinstance(table, dict)]
    if not tables:
        return {}
    matched_index = next(
        (
            index
            for index, table in enumerate(tables)
            if _manifest_table_matches_path(
                table,
                csv_path=path,
                tables_dir=manifest_path.parent / "tables",
            )
        ),
        None,
    )
    if matched_index is None:
        return {}

    table = tables[matched_index]
    previous_table = tables[matched_index - 1] if matched_index > 0 else {}
    next_table = tables[matched_index + 1] if matched_index + 1 < len(tables) else {}
    metadata = {
        "source_kind": "pdf_table_csv",
        "tables_manifest_path": _workspace_relative_path(manifest_path, root_dir),
        "pdf_source_path": str(manifest.get("path") or ""),
        "pdf_table_count": len(tables),
        "previous_pdf_table_name": str(previous_table.get("name") or ""),
        "next_pdf_table_name": str(next_table.get("name") or ""),
    }
    for key in PDF_TABLE_SOURCE_METADATA_KEYS:
        if key in table:
            metadata[f"pdf_table_{key}"] = table[key]
    metadata.setdefault("pdf_table_document_order", matched_index + 1)
    return metadata


def _csv_header_columns(header: list[str]) -> list[str]:
    """Return stable source columns from an already-reviewed CSV header row."""
    return [cell.strip() or f"column_{index}" for index, cell in enumerate(header, start=1)]


def _pdf_table_csv_tables(
    rows: list[list[str]],
    *,
    source_metadata: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build one PDF table CSV block without re-detecting the header row."""
    if not rows:
        return []
    columns = _csv_header_columns(rows[0])
    data_rows = [(row + [""] * len(columns))[: len(columns)] for row in rows[1:]]
    return [
        {
            "name": "table_1",
            "row_start": 1,
            "row_end": len(rows),
            "row_count": len(data_rows),
            "header_row": 1,
            "column_start": 1,
            "column_end": len(columns),
            "columns": columns,
            "rows": data_rows,
            "source_metadata": source_metadata,
        }
    ]


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

    rows, format_info = load_rows(path, sheet=sheet)
    source_metadata = _pdf_table_source_metadata(path, root_dir=root_dir)
    if source_metadata:
        tables = _pdf_table_csv_tables(rows, source_metadata=source_metadata)
        recovered = {
            "path": str(path),
            **tabular_summary(rows, format_info),
            "metadata": [],
            "tables": tables,
        }
        loaded = load_tables_into_sqlite(
            recovered,
            root_dir=root_dir,
        )
        payload = {
            "path": recovered["path"],
            "format": recovered["format"],
            "sheet_name": recovered.get("sheet_name"),
            "status": "loaded" if tables else "empty",
            "artifact_backend": "sqlite",
            "database_path": loaded["database_path"],
            "recovered_table_count": len(tables),
            "excluded_row_hints": [],
            "tables": loaded["tables"],
        }
        formulas = formulas_with_table_refs(
            workbook_formula_cells(path, sheet=recovered.get("sheet_name")),
            recovered_tables=tables,
            loaded_tables=loaded["tables"],
        )
        payload["formula_count"] = len(formulas)
        payload["formulas"] = formulas
        return payload

    metadata, tables = segment_tabular_blocks(
        rows,
        table_sample_rows=None,
        metadata_sample_rows=metadata_rows,
    )
    recovered = {
        "path": str(path),
        **tabular_summary(rows, format_info),
        "metadata": metadata,
        "tables": tables,
    }
    if not recovered["tables"]:
        payload = {
            "path": recovered["path"],
            "format": recovered["format"],
            "sheet_name": recovered.get("sheet_name"),
            "status": "empty",
            "artifact_backend": "sqlite",
            "database_path": "",
            "recovered_table_count": 0,
            "excluded_row_hints": _footer_like_row_hints(recovered["tables"], recovered["metadata"]),
            "tables": [],
            "message": "Tabular extraction completed but did not recover importable tables.",
        }
        formulas = workbook_formula_cells(path, sheet=recovered.get("sheet_name"))
        for formula in formulas:
            formula["table_refs"] = []
        payload["formula_count"] = len(formulas)
        payload["formulas"] = formulas
        return payload

    loaded = load_tables_into_sqlite(
        recovered,
        root_dir=root_dir,
    )

    payload = {
        "path": recovered["path"],
        "format": recovered["format"],
        "sheet_name": recovered.get("sheet_name"),
        "status": "loaded",
        "artifact_backend": "sqlite",
        "database_path": loaded["database_path"],
        "recovered_table_count": len(recovered["tables"]),
        "excluded_row_hints": _footer_like_row_hints(recovered["tables"], recovered["metadata"]),
        "tables": loaded["tables"],
    }
    formulas = formulas_with_table_refs(
        workbook_formula_cells(path, sheet=recovered.get("sheet_name")),
        recovered_tables=recovered["tables"],
        loaded_tables=loaded["tables"],
    )
    payload["formula_count"] = len(formulas)
    payload["formulas"] = formulas
    return payload


def extract_tabular_workbook_sheets(
    path: str | Path,
    *,
    root_dir: str | Path | None = None,
    metadata_rows: int = MAX_METADATA_ROWS,
) -> dict[str, Any]:
    """Extract every worksheet in a workbook into the shared SQLite cache."""
    source_path = Path(path)
    sheet_names = workbook_sheet_names(source_path)
    if not sheet_names:
        raise ValueError(f"All-sheet extraction requires an XLS or XLSX workbook: {source_path}")

    resolved_source_path = source_path.resolve()
    sheets = [
        extract_tabular_file(
            resolved_source_path,
            root_dir=root_dir,
            metadata_rows=metadata_rows,
            sheet=sheet_name,
        )
        for sheet_name in sheet_names
    ]
    recovered_table_count = sum(int(sheet_payload["recovered_table_count"]) for sheet_payload in sheets)
    payload = {
        "path": str(source_path),
        "format": source_path.suffix.lower().removeprefix("."),
        "status": "loaded" if recovered_table_count else "empty",
        "artifact_backend": "sqlite",
        "database_path": next((str(sheet_payload.get("database_path") or "") for sheet_payload in sheets if sheet_payload.get("database_path")), ""),
        "sheet_names": sheet_names,
        "sheet_count": len(sheet_names),
        "recovered_table_count": recovered_table_count,
        "sheets": sheets,
    }
    payload["formula_count"] = sum(int(sheet_payload.get("formula_count", 0)) for sheet_payload in sheets)
    return payload


def extract_tabular_source(
    path: str | Path,
    *,
    root_dir: str | Path | None = None,
    metadata_rows: int = MAX_METADATA_ROWS,
) -> dict[str, Any]:
    """Extract workbooks as all sheets, otherwise extract the single tabular source."""
    source_path = Path(path)
    if not source_path.is_absolute() and root_dir is not None:
        source_path = resolve_root_dir(root_dir=root_dir) / source_path
    if workbook_sheet_names(source_path):
        return extract_tabular_workbook_sheets(
            source_path,
            root_dir=root_dir,
            metadata_rows=metadata_rows,
        )
    return extract_tabular_file(
        source_path,
        root_dir=root_dir,
        metadata_rows=metadata_rows,
    )
