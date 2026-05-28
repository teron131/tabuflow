"""Load reviewed PDF table CSV artifacts into the tabular SQLite catalog."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..tabular import extract_tabular_file
from ..workspace_db import resolve_root_dir, sqlite_database_path
from .common import pdf_artifact_workspace


def _relative_to_root(path: Path, root_dir: Path) -> str:
    """Return a root-relative path when possible."""
    try:
        return str(path.resolve().relative_to(root_dir))
    except ValueError:
        return str(path.resolve())


def _document_order(table: dict[str, Any], fallback: int) -> int:
    """Return the manifest document order, falling back to manifest order."""
    try:
        return int(table.get("document_order"))
    except (TypeError, ValueError):
        return fallback


def _table_csv_path(
    table: dict[str, Any],
    *,
    root_dir: Path,
    tables_dir: Path,
) -> Path:
    """Resolve a table CSV path from a manifest entry."""
    path_text = str(table.get("path") or "")
    if not path_text:
        raise ValueError("PDF table manifest entry is missing `path`.")

    manifest_path = Path(path_text).expanduser()
    candidates = [manifest_path]
    if not manifest_path.is_absolute():
        candidates.extend(
            [
                root_dir / manifest_path,
                tables_dir / manifest_path,
                tables_dir / manifest_path.name,
            ]
        )

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(f"PDF table CSV not found for manifest path: {path_text}")


def _ordered_manifest_tables(manifest: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    """Return manifest table entries in natural document order."""
    tables = [table for table in manifest.get("tables", []) if isinstance(table, dict)]
    return sorted(
        enumerate(tables, start=1),
        key=lambda item: (_document_order(item[1], item[0]), item[0]),
    )


def ingest_pdf_table_artifacts(
    path: str | Path,
    *,
    root_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Import reviewed PDF table CSV artifacts into the shared SQLite catalog."""
    resolved_root_dir = resolve_root_dir(root_dir=root_dir)
    workspace = pdf_artifact_workspace(path, root_dir=resolved_root_dir, create=False)
    if not workspace.tables_manifest_path.is_file():
        raise FileNotFoundError(f"PDF table manifest not found: {workspace.tables_manifest_path}")

    manifest = json.loads(workspace.tables_manifest_path.read_text(encoding="utf-8"))
    sources: list[dict[str, Any]] = []
    loaded_tables: list[dict[str, Any]] = []
    for fallback_order, table in _ordered_manifest_tables(manifest):
        csv_path = _table_csv_path(
            table,
            root_dir=resolved_root_dir,
            tables_dir=workspace.tables_dir,
        )
        extracted = extract_tabular_file(
            csv_path,
            root_dir=resolved_root_dir,
        )
        extracted_tables = list(extracted.get("tables", []))
        loaded_tables.extend(extracted_tables)
        sources.append(
            {
                "document_order": _document_order(table, fallback_order),
                "name": str(table.get("name") or csv_path.stem),
                "path": _relative_to_root(csv_path, resolved_root_dir),
                "status": extracted.get("status"),
                "recovered_table_count": extracted.get("recovered_table_count"),
                "tables": extracted_tables,
            }
        )

    return {
        "status": "loaded" if loaded_tables else "empty",
        "run_type": "pdf-table-ingest",
        "path": str(workspace.pdf_path),
        "artifact_dir": _relative_to_root(workspace.artifact_dir, resolved_root_dir),
        "tables_manifest_path": _relative_to_root(workspace.tables_manifest_path, resolved_root_dir),
        "database_path": str(sqlite_database_path(root_dir=resolved_root_dir)),
        "table_csv_count": len(sources),
        "loaded_table_count": len(loaded_tables),
        "sources": sources,
        "tables": loaded_tables,
    }
