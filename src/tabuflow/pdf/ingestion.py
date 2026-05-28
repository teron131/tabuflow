"""Load reviewed PDF table CSV artifacts into the tabular SQLite catalog."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any

from ..artifacts.relationships import ARTIFACT_FILES_TABLE, ARTIFACT_RELATIONS_TABLE
from ..tabular import extract_tabular_file
from ..workspace_db import (
    SQLITE_CONTENTS_TABLE,
    SQLITE_SOURCES_TABLE,
    quote_identifier,
    resolve_root_dir,
    sqlite_database_path,
    sqlite_write_lock,
)
from .common import PdfArtifactWorkspace, pdf_artifact_workspace

EMPTY_REPLACEMENT = {
    "replaced_source_count": 0,
    "replaced_table_count": 0,
}


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


def _sqlite_table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    """Return whether a SQLite table exists."""
    row = connection.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        [table_name],
    ).fetchone()
    return row is not None


def _workspace_path(path_text: str, *, root_dir: Path) -> Path | None:
    """Return an absolute workspace path from metadata text."""
    if not path_text:
        return None
    path = Path(path_text).expanduser()
    if not path.is_absolute():
        path = root_dir / path
    return path


def _same_workspace_path(path_text: str, expected_path: Path, *, root_dir: Path) -> bool:
    """Return whether text points to the expected workspace path."""
    path = _workspace_path(path_text, root_dir=root_dir)
    if path is None:
        return False
    try:
        return path.resolve() == expected_path.resolve()
    except OSError:
        return False


def _path_under(path_text: str, directory: Path, *, root_dir: Path) -> bool:
    """Return whether text points inside a workspace directory."""
    path = _workspace_path(path_text, root_dir=root_dir)
    if path is None:
        return False
    try:
        path.resolve().relative_to(directory.resolve())
    except (OSError, ValueError):
        return False
    return True


def _source_row_belongs_to_pdf(
    source_path: str,
    source_metadata_json: str,
    *,
    workspace: PdfArtifactWorkspace,
) -> bool:
    """Return whether one source mapping came from this PDF's table workspace."""
    try:
        source_metadata = json.loads(source_metadata_json)
    except json.JSONDecodeError:
        source_metadata = {}
    if not isinstance(source_metadata, dict):
        source_metadata = {}

    return (
        _path_under(source_path, workspace.tables_dir, root_dir=workspace.root_dir)
        or _same_workspace_path(str(source_metadata.get("pdf_source_path") or ""), workspace.pdf_path, root_dir=workspace.root_dir)
        or _same_workspace_path(str(source_metadata.get("tables_manifest_path") or ""), workspace.tables_manifest_path, root_dir=workspace.root_dir)
    )


def _delete_values(
    connection: sqlite3.Connection,
    *,
    table_name: str,
    column_name: str,
    values: set[str],
) -> None:
    """Delete rows by a bounded set of values."""
    if not values:
        return
    placeholders = ", ".join("?" for _ in values)
    connection.execute(
        f"""
        DELETE FROM {quote_identifier(table_name)}
        WHERE {quote_identifier(column_name)} IN ({placeholders})
        """,
        sorted(values),
    )


def _delete_artifact_relations(
    connection: sqlite3.Connection,
    *,
    fingerprints: set[str],
    artifact_names: set[str],
    file_paths: set[str],
) -> None:
    """Delete stale relationships for replaced PDF table artifacts."""
    if not _sqlite_table_exists(connection, ARTIFACT_RELATIONS_TABLE):
        return
    values_by_kind = {
        "fingerprint": fingerprints,
        "artifact": artifact_names,
        "file": file_paths,
    }
    for kind, values in values_by_kind.items():
        if not values:
            continue
        placeholders = ", ".join("?" for _ in values)
        parameters = [kind, *sorted(values), kind, *sorted(values)]
        connection.execute(
            f"""
            DELETE FROM {quote_identifier(ARTIFACT_RELATIONS_TABLE)}
            WHERE (from_kind = ? AND from_id IN ({placeholders}))
               OR (to_kind = ? AND to_id IN ({placeholders}))
            """,
            parameters,
        )


def _pdf_replaced_source_rows(
    connection: sqlite3.Connection,
    *,
    workspace: PdfArtifactWorkspace,
) -> list[sqlite3.Row]:
    """Return previous source rows imported from this PDF table workspace."""
    source_rows = connection.execute(
        f"""
        SELECT rowid, source_path, fingerprint, source_metadata_json
        FROM {quote_identifier(SQLITE_SOURCES_TABLE)}
        """
    ).fetchall()
    return [
        row
        for row in source_rows
        if _source_row_belongs_to_pdf(
            str(row["source_path"]),
            str(row["source_metadata_json"]),
            workspace=workspace,
        )
    ]


def _drop_unreferenced_content_tables(
    connection: sqlite3.Connection,
    *,
    fingerprints: set[str],
) -> set[str]:
    """Drop content tables whose fingerprints are no longer referenced."""
    if not _sqlite_table_exists(connection, SQLITE_CONTENTS_TABLE):
        return set()

    removed_table_names: set[str] = set()
    for fingerprint in sorted(fingerprints):
        remaining = connection.execute(
            f"SELECT 1 FROM {quote_identifier(SQLITE_SOURCES_TABLE)} WHERE fingerprint = ? LIMIT 1",
            [fingerprint],
        ).fetchone()
        if remaining is not None:
            continue
        content_row = connection.execute(
            f"SELECT table_name FROM {quote_identifier(SQLITE_CONTENTS_TABLE)} WHERE fingerprint = ?",
            [fingerprint],
        ).fetchone()
        if content_row is None:
            continue
        table_name = str(content_row["table_name"])
        typed_view_name = f"{table_name}_typed"
        connection.execute(f"DROP VIEW IF EXISTS {quote_identifier(typed_view_name)}")
        connection.execute(f"DROP TABLE IF EXISTS {quote_identifier(table_name)}")
        connection.execute(
            f"DELETE FROM {quote_identifier(SQLITE_CONTENTS_TABLE)} WHERE fingerprint = ?",
            [fingerprint],
        )
        removed_table_names.update({table_name, typed_view_name})
    return removed_table_names


def _delete_replaced_source_rows(
    connection: sqlite3.Connection,
    *,
    replaced_rows: list[sqlite3.Row],
    workspace: PdfArtifactWorkspace,
) -> tuple[set[str], set[str]]:
    """Delete stale source rows and return source paths and fingerprints."""
    row_ids = {str(row["rowid"]) for row in replaced_rows}
    source_paths = {str(row["source_path"]) for row in replaced_rows}
    source_paths.add(str(workspace.pdf_path))
    fingerprints = {str(row["fingerprint"]) for row in replaced_rows}
    _delete_values(
        connection,
        table_name=SQLITE_SOURCES_TABLE,
        column_name="rowid",
        values=row_ids,
    )
    return source_paths, fingerprints


def _delete_replaced_artifact_metadata(
    connection: sqlite3.Connection,
    *,
    fingerprints: set[str],
    artifact_names: set[str],
    file_paths: set[str],
) -> None:
    """Delete relationship metadata for replaced PDF table imports."""
    _delete_artifact_relations(
        connection,
        fingerprints=fingerprints,
        artifact_names=artifact_names,
        file_paths=file_paths,
    )
    if _sqlite_table_exists(connection, ARTIFACT_FILES_TABLE):
        _delete_values(
            connection,
            table_name=ARTIFACT_FILES_TABLE,
            column_name="path",
            values=file_paths,
        )


def _replace_pdf_table_ingest_state(workspace: PdfArtifactWorkspace) -> dict[str, int]:
    """Remove previous SQLite imports for this PDF before ingesting the current manifest."""
    database_path = sqlite_database_path(root_dir=workspace.root_dir)
    if not database_path.exists():
        return EMPTY_REPLACEMENT

    with sqlite_write_lock(database_path):
        connection = sqlite3.connect(str(database_path))
        connection.row_factory = sqlite3.Row
        try:
            if not _sqlite_table_exists(connection, SQLITE_SOURCES_TABLE):
                return EMPTY_REPLACEMENT
            replaced_rows = _pdf_replaced_source_rows(connection, workspace=workspace)
            if not replaced_rows:
                return EMPTY_REPLACEMENT

            source_paths, fingerprints = _delete_replaced_source_rows(
                connection,
                replaced_rows=replaced_rows,
                workspace=workspace,
            )
            removed_table_names = _drop_unreferenced_content_tables(
                connection,
                fingerprints=fingerprints,
            )
            _delete_replaced_artifact_metadata(
                connection,
                fingerprints=fingerprints,
                artifact_names=removed_table_names,
                file_paths=source_paths,
            )
            connection.commit()
        finally:
            connection.close()

    return {
        "replaced_source_count": len(replaced_rows),
        "replaced_table_count": len(removed_table_names) // 2,
    }


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
    replacement = _replace_pdf_table_ingest_state(workspace)
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
        **replacement,
        "table_csv_count": len(sources),
        "loaded_table_count": len(loaded_tables),
        "sources": sources,
        "tables": loaded_tables,
    }
