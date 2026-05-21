"""SQLite catalog snapshot loading and source lineage helpers."""

from __future__ import annotations

from contextlib import closing
from functools import lru_cache
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, cast

from ..tabular.storage import SQLITE_CONTENTS_TABLE, SQLITE_SOURCES_TABLE, quote_identifier
from .database import open_read_only_connection

_SQL_ARTIFACT_MASTER_SQL = """
SELECT name, type, sql
FROM sqlite_master
WHERE type IN ('table', 'view')
  AND name NOT LIKE 'sqlite_%'
ORDER BY type, name
"""


class CatalogMetadataError(RuntimeError):
    """Raised when a queryable catalog artifact is missing required lineage."""


def _sql_artifact_row_count(connection: sqlite3.Connection, sql_artifact_name: str) -> int | None:
    """Return a SQLite artifact row count when catalog metadata is unavailable."""
    try:
        row = connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(sql_artifact_name)}").fetchone()
    except (sqlite3.Error, sqlite3.Warning):
        return None
    if row is None:
        return None
    return cast(int, row[0])


def _sqlite_object_names(connection: sqlite3.Connection) -> set[str]:
    """Return all user-visible table and view names in a SQLite database."""
    return {
        cast(str, row[0])
        for row in connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type IN ('table', 'view')
              AND name NOT LIKE 'sqlite_%'
            """
        ).fetchall()
    }


def _fetch_catalog_metadata(
    connection: sqlite3.Connection,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, list[dict[str, Any]]],
]:
    """Load shared tabular catalog metadata for list, describe, and suggest helpers."""
    content_query = f"SELECT content_id, table_name, source_format, row_count, column_schema_json FROM {SQLITE_CONTENTS_TABLE}"
    content_rows = {
        cast(str, row[1]): {
            "content_id": cast(str, row[0]),
            "table_name": cast(str, row[1]),
            "source_format": cast(str, row[2]),
            "row_count": cast(int, row[3]),
            "content_schema": json.loads(cast(str, row[4])),
        }
        for row in connection.execute(content_query).fetchall()
    }
    source_rows: dict[str, list[dict[str, Any]]] = {}
    source_query = f"""
    SELECT content_id, source_path, source_format, source_sheet_name, source_table_name, fingerprint
    FROM {SQLITE_SOURCES_TABLE}
    ORDER BY source_path, source_table_name
    """
    for row in connection.execute(source_query).fetchall():
        content_id = cast(str, row[0])
        source_rows.setdefault(content_id, []).append(
            {
                "source_path": cast(str, row[1]),
                "source_format": cast(str, row[2]),
                "source_sheet_name": cast(str, row[3]),
                "source_table_name": cast(str, row[4]),
                "fingerprint": cast(str, row[5]),
            }
        )
    return content_rows, source_rows


def _catalog_state(
    connection: sqlite3.Connection,
) -> tuple[
    bool,
    dict[str, dict[str, Any]],
    dict[str, list[dict[str, Any]]],
]:
    """Return whether the tabular catalog exists plus its cached metadata."""
    if not _has_catalog(connection):
        return False, {}, {}
    content_rows, source_rows = _fetch_catalog_metadata(connection)
    return True, content_rows, source_rows


def _has_catalog(connection: sqlite3.Connection) -> bool:
    """Return whether the shared tabular catalog exists in this database."""
    object_names = _sqlite_object_names(connection)
    return SQLITE_CONTENTS_TABLE in object_names and SQLITE_SOURCES_TABLE in object_names


def classify_sql_artifact(
    name: str,
    content_table_names: set[str] | None = None,
) -> str:
    """Classify a SQLite artifact using current naming conventions."""
    if name in (SQLITE_CONTENTS_TABLE, SQLITE_SOURCES_TABLE):
        return "internal_catalog"
    content_tables = content_table_names or set()
    if name in content_tables:
        return "raw_content_table"
    if name.endswith("_typed") and name.removesuffix("_typed") in content_tables:
        return "typed_content_view"
    return "view_or_table"


def _base_table_name(name: str, kind: str) -> str:
    """Map a view name back to its catalog table name."""
    if kind == "typed_content_view":
        return name.removesuffix("_typed")
    return name


def _sql_artifact_source_paths(
    *,
    name: str,
    kind: str,
    content_rows: dict[str, dict[str, Any]],
    source_rows: dict[str, list[dict[str, Any]]],
) -> tuple[
    dict[str, Any] | None,
    list[dict[str, Any]],
    list[str],
]:
    """Return catalog metadata, source mappings, and source paths for one artifact."""
    content_metadata = content_rows.get(_base_table_name(name, kind))
    if content_metadata is None:
        return None, [], []
    source_mappings = _dedupe_source_mappings(source_rows.get(cast(str, content_metadata["content_id"]), []))
    if not source_mappings:
        raise CatalogMetadataError(f"Source metadata is missing for queryable artifact `{content_metadata['table_name']}`.")
    source_paths = source_paths_from_mappings(source_mappings)
    return content_metadata, source_mappings, source_paths


def source_paths_from_mappings(source_mappings: list[dict[str, Any]]) -> list[str]:
    """Return non-empty source paths while preserving first-seen order."""
    return list(dict.fromkeys(source_path for mapping in source_mappings if (source_path := str(mapping.get("source_path") or "").strip())))


def path_match_reason(source_path: str, requested_source: str) -> str | None:
    """Return why a stored source path matches the requested source path."""
    source_text = source_path.strip()
    requested_text = requested_source.strip()
    if not source_text or not requested_text:
        return None
    if source_text == requested_text:
        return "exact"

    requested_path = Path(requested_text).expanduser()
    source_path_obj = Path(source_text).expanduser()
    requested_resolved = str(requested_path.resolve()) if requested_path.exists() else ""
    source_resolved = str(source_path_obj.resolve()) if source_path_obj.exists() else ""
    if requested_resolved and source_resolved and requested_resolved == source_resolved:
        return "resolved"
    if requested_resolved and source_text == requested_resolved:
        return "resolved_requested"
    if source_resolved and source_resolved == requested_text:
        return "resolved_source"

    normalized_source = source_text.replace("\\", "/")
    normalized_requested = requested_text.replace("\\", "/")
    if normalized_source.endswith(normalized_requested) or normalized_requested.endswith(normalized_source):
        return "suffix"
    if Path(normalized_source).name == Path(normalized_requested).name:
        return "filename"
    return None


SQL_ARTIFACT_REFERENCE_PATTERN = re.compile(
    r'\b(?:FROM|JOIN)\s+(?:"([^"]+)"|`([^`]+)`|\[([^\]]+)\]|([A-Za-z_][\w$]*))',
    re.IGNORECASE,
)


def _referenced_sql_artifact_names(
    create_sql: str | None,
    *,
    available_sql_artifacts: set[str],
    current_sql_artifact: str,
) -> list[str]:
    """Return known SQLite artifacts referenced by a stored SQL artifact."""
    if not create_sql:
        return []
    references: list[str] = []
    for match in SQL_ARTIFACT_REFERENCE_PATTERN.finditer(create_sql):
        reference = next((value for value in match.groups() if value), "")
        if reference and reference != current_sql_artifact and reference in available_sql_artifacts:
            references.append(reference)
    return list(dict.fromkeys(references))


def _dedupe_source_mappings(source_mappings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deduplicate source mappings while preserving first-seen order."""
    seen: set[tuple[str, str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for mapping in source_mappings:
        source_path = str(mapping.get("source_path") or "").strip()
        if not source_path:
            continue
        key = (
            source_path,
            str(mapping.get("source_sheet_name") or ""),
            str(mapping.get("source_table_name") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(mapping)
    return deduped


def _lineage_source_mappings(
    sql_artifact_info: dict[str, Any],
    sql_artifacts_by_name: dict[str, dict[str, Any]],
    *,
    visited: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return direct source mappings or inherit mappings from upstream SQL artifacts."""
    direct_mappings = _dedupe_source_mappings(cast(list[dict[str, Any]], sql_artifact_info["source_mappings"]))
    if direct_mappings:
        return direct_mappings

    visited_sql_artifacts = set() if visited is None else visited
    sql_artifact_name = cast(str, sql_artifact_info["name"])
    if sql_artifact_name in visited_sql_artifacts:
        return []
    visited_sql_artifacts.add(sql_artifact_name)

    source_mappings: list[dict[str, Any]] = []
    for referenced_name in cast(list[str], sql_artifact_info.get("source_sql_artifact_names", [])):
        referenced_sql_artifact = sql_artifacts_by_name.get(referenced_name)
        if referenced_sql_artifact is None:
            continue
        source_mappings.extend(
            _lineage_source_mappings(
                referenced_sql_artifact,
                sql_artifacts_by_name,
                visited=visited_sql_artifacts,
            )
        )
    return _dedupe_source_mappings(source_mappings)


def _database_cache_key(
    database_path: Path,
) -> tuple[
    str,
    int,
    int,
]:
    """Return a cache key that invalidates when the SQLite file changes."""
    resolved_path = database_path.resolve()
    stat_result = resolved_path.stat()
    return str(resolved_path), stat_result.st_mtime_ns, stat_result.st_size


@lru_cache(maxsize=8)
def _cached_database_catalog(
    resolved_path: str,
    mtime_ns: int,
    size_bytes: int,
) -> dict[str, Any]:
    """Load one cached SQLite catalog snapshot."""
    del mtime_ns, size_bytes
    database_path = Path(resolved_path)
    with closing(open_read_only_connection(database_path)) as connection:
        has_catalog, content_rows, source_rows = _catalog_state(connection)
        content_table_names = set(content_rows)
        sql_artifacts = []
        sql_artifacts_by_name: dict[str, dict[str, Any]] = {}
        for master_row in connection.execute(_SQL_ARTIFACT_MASTER_SQL).fetchall():
            name = cast(str, master_row[0])
            sql_artifact_type = cast(str, master_row[1])
            create_sql = cast(Any, master_row[2])
            kind = classify_sql_artifact(name, content_table_names=content_table_names)
            columns = [
                {
                    "name": cast(str, row[1]),
                    "type": cast(str, row[2]),
                    "not_null": bool(row[3]),
                    "default_value": row[4],
                    "primary_key_position": cast(int, row[5]),
                }
                for row in connection.execute(f"PRAGMA table_info({quote_identifier(name)})").fetchall()
            ]
            content_metadata, source_mappings, source_paths = _sql_artifact_source_paths(
                name=name,
                kind=kind,
                content_rows=content_rows,
                source_rows=source_rows,
            )
            row_count = None if content_metadata is None else content_metadata["row_count"]
            if row_count is None:
                row_count = _sql_artifact_row_count(connection, name)
            sql_artifact_info = {
                "name": name,
                "type": sql_artifact_type,
                "kind": kind,
                "create_sql": create_sql,
                "columns": columns,
                "content_id": None if content_metadata is None else content_metadata["content_id"],
                "content_schema": None if content_metadata is None else content_metadata["content_schema"],
                "row_count": row_count,
                "source_mappings": source_mappings,
                "source_paths": source_paths,
                "source_sql_artifact_names": [],
            }
            sql_artifacts.append(sql_artifact_info)
            sql_artifacts_by_name[name] = sql_artifact_info
        available_sql_artifacts = set(sql_artifacts_by_name)
        for sql_artifact_info in sql_artifacts:
            source_sql_artifact_names = _referenced_sql_artifact_names(
                cast(str | None, sql_artifact_info["create_sql"]),
                available_sql_artifacts=available_sql_artifacts,
                current_sql_artifact=cast(str, sql_artifact_info["name"]),
            )
            sql_artifact_info["source_sql_artifact_names"] = source_sql_artifact_names
        for sql_artifact_info in sql_artifacts:
            lineage_source_mappings = _lineage_source_mappings(
                sql_artifact_info,
                sql_artifacts_by_name,
            )
            sql_artifact_info["source_mappings"] = lineage_source_mappings
            sql_artifact_info["source_paths"] = source_paths_from_mappings(lineage_source_mappings)
        return {
            "has_catalog": has_catalog,
            "sql_artifacts": sql_artifacts,
            "sql_artifacts_by_name": sql_artifacts_by_name,
        }


def database_catalog(database_path: Path) -> dict[str, Any]:
    """Return the cached database catalog for one SQLite file."""
    return _cached_database_catalog(*_database_cache_key(database_path))
