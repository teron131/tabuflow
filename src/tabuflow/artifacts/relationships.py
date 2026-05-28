"""Current-state artifact file and relationship metadata."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, cast

from ..workspace_db import quote_identifier

ARTIFACT_FILES_TABLE = "_artifact_files"
ARTIFACT_SQL_FILES_TABLE = "_artifact_sql_files"
ARTIFACT_VIEWS_TABLE = "_artifact_views"
ARTIFACT_RELATIONS_TABLE = "_artifact_relations"
ARTIFACT_RELATION_TABLES = {
    ARTIFACT_FILES_TABLE,
    ARTIFACT_SQL_FILES_TABLE,
    ARTIFACT_VIEWS_TABLE,
    ARTIFACT_RELATIONS_TABLE,
}
ARTIFACT_REFERENCE_PATTERN = re.compile(
    r'\b(?:FROM|JOIN)\s+(?:"([^"]+)"|`([^`]+)`|\[([^\]]+)\]|([A-Za-z_][\w$]*))',
    re.IGNORECASE,
)


def ensure_artifact_relationship_tables(connection: sqlite3.Connection) -> None:
    """Create current-state artifact relationship metadata tables."""
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {quote_identifier(ARTIFACT_FILES_TABLE)} (
            path TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            file_hash TEXT,
            label TEXT,
            description TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {quote_identifier(ARTIFACT_SQL_FILES_TABLE)} (
            path TEXT PRIMARY KEY,
            sql_hash TEXT NOT NULL,
            defines_artifact_name TEXT,
            description TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {quote_identifier(ARTIFACT_VIEWS_TABLE)} (
            view_name TEXT PRIMARY KEY,
            sql_hash TEXT NOT NULL,
            sql_file_path TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {quote_identifier(ARTIFACT_RELATIONS_TABLE)} (
            from_kind TEXT NOT NULL,
            from_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            to_kind TEXT NOT NULL,
            to_id TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (from_kind, from_id, relation, to_kind, to_id)
        )
        """
    )


def _upsert_artifact_file(
    connection: sqlite3.Connection,
    *,
    path: str,
    kind: str,
    file_hash: str | None = None,
    label: str | None = None,
    description: str | None = None,
) -> None:
    """Register a file that participates in the artifact graph."""
    ensure_artifact_relationship_tables(connection)
    normalized_path = str(Path(path).expanduser())
    connection.execute(
        f"""
        INSERT INTO {quote_identifier(ARTIFACT_FILES_TABLE)}
        (path, kind, file_hash, label, description, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            kind = excluded.kind,
            file_hash = excluded.file_hash,
            label = excluded.label,
            description = excluded.description,
            updated_at = excluded.updated_at
        """,
        [
            normalized_path,
            kind,
            file_hash,
            label or Path(normalized_path).name,
            description,
            datetime.now(UTC).isoformat(timespec="seconds"),
        ],
    )


def _upsert_relation(
    connection: sqlite3.Connection,
    *,
    from_kind: str,
    from_id: str,
    relation: str,
    to_kind: str,
    to_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Register one current relationship between files, fingerprints, and artifacts."""
    ensure_artifact_relationship_tables(connection)
    connection.execute(
        f"""
        INSERT INTO {quote_identifier(ARTIFACT_RELATIONS_TABLE)}
        (from_kind, from_id, relation, to_kind, to_id, metadata_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(from_kind, from_id, relation, to_kind, to_id) DO UPDATE SET
            metadata_json = excluded.metadata_json,
            updated_at = excluded.updated_at
        """,
        [
            from_kind,
            from_id,
            relation,
            to_kind,
            to_id,
            json.dumps(metadata or {}, sort_keys=True, separators=(",", ":")),
            datetime.now(UTC).isoformat(timespec="seconds"),
        ],
    )


def register_source_table_relationships(
    connection: sqlite3.Connection,
    *,
    source_path: str,
    source_format: str,
    source_sheet_name: str,
    source_table_name: str,
    fingerprint: str,
    table_name: str,
    typed_view_name: str,
    source_metadata: dict[str, Any] | None = None,
) -> None:
    """Register source-file, fingerprint, raw table, and typed-view relationships."""
    _upsert_artifact_file(
        connection,
        path=source_path,
        kind="source",
        description=f"{source_format} source",
    )
    if source_metadata and (pdf_source_path := str(source_metadata.get("pdf_source_path") or "").strip()):
        _upsert_artifact_file(
            connection,
            path=pdf_source_path,
            kind="pdf_source",
            description="PDF source for extracted table CSV",
        )
    relation_metadata = {
        "source_format": source_format,
        "source_sheet_name": source_sheet_name,
        "source_table_name": source_table_name,
    }
    if source_metadata:
        relation_metadata["source_metadata"] = source_metadata
    _upsert_relation(
        connection,
        from_kind="file",
        from_id=source_path,
        relation="produced",
        to_kind="fingerprint",
        to_id=fingerprint,
        metadata=relation_metadata,
    )
    if source_metadata and (pdf_source_path := str(source_metadata.get("pdf_source_path") or "").strip()):
        _upsert_relation(
            connection,
            from_kind="file",
            from_id=pdf_source_path,
            relation="produced",
            to_kind="fingerprint",
            to_id=fingerprint,
            metadata=relation_metadata,
        )
    _upsert_relation(
        connection,
        from_kind="fingerprint",
        from_id=fingerprint,
        relation="stored_as",
        to_kind="artifact",
        to_id=table_name,
    )
    _upsert_relation(
        connection,
        from_kind="artifact",
        from_id=table_name,
        relation="typed_as",
        to_kind="artifact",
        to_id=typed_view_name,
    )
    _upsert_relation(
        connection,
        from_kind="artifact",
        from_id=typed_view_name,
        relation="typed_from",
        to_kind="artifact",
        to_id=table_name,
    )


def referenced_artifact_names(
    sql: str,
    *,
    available_artifact_names: set[str],
    current_artifact_name: str,
) -> list[str]:
    """Return known SQLite artifact names referenced by a SQL statement."""
    references: list[str] = []
    for match in ARTIFACT_REFERENCE_PATTERN.finditer(sql):
        reference = next((value for value in match.groups() if value), "")
        if reference and reference != current_artifact_name and reference in available_artifact_names:
            references.append(reference)
    return list(dict.fromkeys(references))


def _delete_saved_view_definition_metadata(
    connection: sqlite3.Connection,
    *,
    view_name: str,
    sql_file_path: str | None,
) -> None:
    """Remove stale SQL-file definition metadata for one saved view."""
    connection.execute(
        f"""
        DELETE FROM {quote_identifier(ARTIFACT_RELATIONS_TABLE)}
        WHERE to_kind = 'artifact'
          AND to_id = ?
          AND relation = 'defines'
        """,
        [view_name],
    )
    if sql_file_path is None:
        connection.execute(
            f"""
            DELETE FROM {quote_identifier(ARTIFACT_SQL_FILES_TABLE)}
            WHERE defines_artifact_name = ?
            """,
            [view_name],
        )
        return
    connection.execute(
        f"""
        DELETE FROM {quote_identifier(ARTIFACT_SQL_FILES_TABLE)}
        WHERE defines_artifact_name = ?
          AND path != ?
        """,
        [view_name, sql_file_path],
    )


def _upsert_saved_view_metadata(
    connection: sqlite3.Connection,
    *,
    view_name: str,
    sql_hash: str,
    sql_file_path: str | None,
    updated_at: str,
) -> None:
    """Record the current SQL definition metadata for one saved view."""
    connection.execute(
        f"""
        INSERT INTO {quote_identifier(ARTIFACT_VIEWS_TABLE)}
        (view_name, sql_hash, sql_file_path, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(view_name) DO UPDATE SET
            sql_hash = excluded.sql_hash,
            sql_file_path = excluded.sql_file_path,
            updated_at = excluded.updated_at
        """,
        [view_name, sql_hash, sql_file_path, updated_at],
    )


def _upsert_saved_view_sql_file(
    connection: sqlite3.Connection,
    *,
    view_name: str,
    sql_hash: str,
    sql_file_path: str | None,
    updated_at: str,
) -> None:
    """Record the SQL file that defines a saved view when one is available."""
    if sql_file_path is None:
        return

    description = f"Defines SQLite artifact {view_name}"
    _upsert_artifact_file(
        connection,
        path=sql_file_path,
        kind="sql",
        file_hash=sql_hash,
        description=description,
    )
    connection.execute(
        f"""
        INSERT INTO {quote_identifier(ARTIFACT_SQL_FILES_TABLE)}
        (path, sql_hash, defines_artifact_name, description, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            sql_hash = excluded.sql_hash,
            defines_artifact_name = excluded.defines_artifact_name,
            description = excluded.description,
            updated_at = excluded.updated_at
        """,
        [
            sql_file_path,
            sql_hash,
            view_name,
            description,
            updated_at,
        ],
    )
    _upsert_relation(
        connection,
        from_kind="file",
        from_id=sql_file_path,
        relation="defines",
        to_kind="artifact",
        to_id=view_name,
        metadata={"sql_hash": sql_hash},
    )


def _replace_saved_view_dependencies(
    connection: sqlite3.Connection,
    *,
    view_name: str,
    sql_hash: str,
    dependency_names: list[str],
) -> None:
    """Replace dependency relationships for one saved view."""
    connection.execute(
        f"""
        DELETE FROM {quote_identifier(ARTIFACT_RELATIONS_TABLE)}
        WHERE from_kind = 'artifact'
          AND from_id = ?
          AND relation = 'depends_on'
        """,
        [view_name],
    )
    for dependency_name in dependency_names:
        _upsert_relation(
            connection,
            from_kind="artifact",
            from_id=view_name,
            relation="depends_on",
            to_kind="artifact",
            to_id=dependency_name,
            metadata={"sql_hash": sql_hash},
        )


def register_saved_view_relationships(
    connection: sqlite3.Connection,
    *,
    view_name: str,
    sql: str,
    sql_file_path: str | Path | None,
    dependency_names: list[str],
) -> None:
    """Register SQL file, view, and dependency relationships for a saved view."""
    ensure_artifact_relationship_tables(connection)
    sql_hash = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    updated_at = datetime.now(UTC).isoformat(timespec="seconds")
    normalized_file_path = None if sql_file_path is None else str(Path(sql_file_path).expanduser())

    _delete_saved_view_definition_metadata(
        connection,
        view_name=view_name,
        sql_file_path=normalized_file_path,
    )
    _upsert_saved_view_metadata(
        connection,
        view_name=view_name,
        sql_hash=sql_hash,
        sql_file_path=normalized_file_path,
        updated_at=updated_at,
    )
    _upsert_saved_view_sql_file(
        connection,
        view_name=view_name,
        sql_hash=sql_hash,
        sql_file_path=normalized_file_path,
        updated_at=updated_at,
    )
    _replace_saved_view_dependencies(
        connection,
        view_name=view_name,
        sql_hash=sql_hash,
        dependency_names=dependency_names,
    )


def artifact_relationship_metadata(
    connection: sqlite3.Connection,
    *,
    artifact_name: str,
    source_mappings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return direct relationships and related files for one SQLite artifact."""
    table_names = {
        cast(str, row[0])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
            """
        ).fetchall()
    }
    if not ARTIFACT_RELATION_TABLES.issubset(table_names):
        return {
            "relations": [],
            "related_files": [],
            "related_file_count": 0,
        }

    rows = connection.execute(
        f"""
        SELECT from_kind, from_id, relation, to_kind, to_id, metadata_json, updated_at
        FROM {quote_identifier(ARTIFACT_RELATIONS_TABLE)}
        WHERE (from_kind = 'artifact' AND from_id = ?)
           OR (to_kind = 'artifact' AND to_id = ?)
        ORDER BY from_kind, from_id, relation, to_kind, to_id
        """,
        [artifact_name, artifact_name],
    ).fetchall()
    direct_relations = [
        {
            "from_kind": cast(str, row[0]),
            "from_id": cast(str, row[1]),
            "relation": cast(str, row[2]),
            "to_kind": cast(str, row[3]),
            "to_id": cast(str, row[4]),
            "metadata": json.loads(cast(str, row[5])),
            "updated_at": cast(str, row[6]),
        }
        for row in rows
    ]

    related_file_paths = {str(mapping.get("source_path") or "").strip() for mapping in source_mappings if str(mapping.get("source_path") or "").strip()}
    for mapping in source_mappings:
        source_metadata = mapping.get("source_metadata")
        if not isinstance(source_metadata, dict):
            continue
        pdf_source_path = str(source_metadata.get("pdf_source_path") or "").strip()
        if pdf_source_path:
            related_file_paths.add(pdf_source_path)
    for relation in direct_relations:
        if relation["from_kind"] == "file":
            related_file_paths.add(cast(str, relation["from_id"]))
        if relation["to_kind"] == "file":
            related_file_paths.add(cast(str, relation["to_id"]))

    related_files = []
    if related_file_paths:
        placeholders = ", ".join("?" for _ in related_file_paths)
        file_rows = connection.execute(
            f"""
            SELECT path, kind, file_hash, label, description, updated_at
            FROM {quote_identifier(ARTIFACT_FILES_TABLE)}
            WHERE path IN ({placeholders})
            ORDER BY kind, path
            """,
            sorted(related_file_paths),
        ).fetchall()
        related_files = [
            {
                "path": cast(str, row[0]),
                "kind": cast(str, row[1]),
                "file_hash": row[2],
                "label": row[3],
                "description": row[4],
                "updated_at": cast(str, row[5]),
            }
            for row in file_rows
        ]

    return {
        "relations": direct_relations,
        "related_files": related_files,
        "related_file_count": len(related_files),
    }
