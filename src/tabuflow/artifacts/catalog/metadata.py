"""SQLite catalog snapshot loading and source lineage helpers."""

from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass, field, replace
from functools import lru_cache
import json
from pathlib import Path
import sqlite3
from typing import Any, cast

from ...workspace_db import SQLITE_CONTENTS_TABLE, SQLITE_SOURCES_TABLE, quote_identifier
from ..database import open_read_only_connection
from ..relationships import ARTIFACT_RELATION_TABLES, referenced_artifact_names

_SQL_ARTIFACT_MASTER_SQL = """
SELECT name, type, sql
FROM sqlite_master
WHERE type IN ('table', 'view')
  AND name NOT LIKE 'sqlite_%'
ORDER BY type, name
"""


class CatalogMetadataError(RuntimeError):
    """Raised when a queryable catalog artifact is missing required lineage."""


@dataclass(frozen=True)
class CatalogContentMetadata:
    """Metadata for one raw content table in the tabular catalog."""

    fingerprint: str
    row_count: int
    content_schema: dict[str, Any]


@dataclass(frozen=True)
class SqlArtifactInfo:
    """Catalog metadata for one queryable SQLite table or view."""

    name: str
    sqlite_type: str
    kind: str
    create_sql: str | None
    columns: list[dict[str, Any]]
    fingerprint: str | None
    content_schema: dict[str, Any] | None
    row_count: int | None
    source_mappings: list[dict[str, Any]]
    source_paths: list[str]
    source_sql_artifact_names: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DatabaseCatalog:
    """Cached SQLite catalog snapshot used by artifact discovery tools."""

    sql_artifacts: list[SqlArtifactInfo]
    sql_artifacts_by_name: dict[str, SqlArtifactInfo]

    def visible_sql_artifacts(self, *, include_internal: bool) -> list[SqlArtifactInfo]:
        """Return artifacts visible to public list, source, and suggestion calls."""
        if include_internal:
            return self.sql_artifacts
        return [artifact for artifact in self.sql_artifacts if artifact.kind != "internal_catalog"]


def _artifact_row_count(
    connection: sqlite3.Connection,
    artifact_name: str,
) -> int | None:
    """Return a SQLite artifact row count when catalog metadata is unavailable."""
    try:
        row = connection.execute(f"SELECT COUNT(*) FROM {quote_identifier(artifact_name)}").fetchone()
    except (sqlite3.Error, sqlite3.Warning):
        return None
    if row is None:
        return None
    return cast(int, row[0])


def _catalog_table_columns(
    connection: sqlite3.Connection,
    table_name: str,
) -> set[str]:
    """Return catalog table columns for schema-aware reads."""
    return {cast(str, row[1]) for row in connection.execute(f"PRAGMA table_info({quote_identifier(table_name)})").fetchall()}


def _catalog_fingerprint_column(columns: set[str]) -> str:
    """Return the column that stores exact table fingerprints in this catalog."""
    if "fingerprint" in columns:
        return "fingerprint"
    raise CatalogMetadataError("Tabular catalog metadata is missing a fingerprint column.")


def _fetch_catalog_metadata(
    connection: sqlite3.Connection,
) -> tuple[
    dict[str, CatalogContentMetadata],
    dict[str, list[dict[str, Any]]],
]:
    """Load shared tabular catalog metadata for list, describe, and suggest helpers."""
    content_fingerprint_column = _catalog_fingerprint_column(_catalog_table_columns(connection, SQLITE_CONTENTS_TABLE))
    source_fingerprint_column = _catalog_fingerprint_column(_catalog_table_columns(connection, SQLITE_SOURCES_TABLE))
    content_query = f"SELECT {content_fingerprint_column}, table_name, row_count, column_schema_json FROM {SQLITE_CONTENTS_TABLE}"
    content_rows = {
        cast(str, row[1]): CatalogContentMetadata(
            fingerprint=cast(str, row[0]),
            row_count=cast(int, row[2]),
            content_schema=json.loads(cast(str, row[3])),
        )
        for row in connection.execute(content_query).fetchall()
    }
    source_rows: dict[str, list[dict[str, Any]]] = {}
    source_query = f"""
    SELECT {source_fingerprint_column}, source_path, source_format, source_sheet_name, source_table_name, source_metadata_json
    FROM {SQLITE_SOURCES_TABLE}
    ORDER BY source_path, source_table_name
    """
    for row in connection.execute(source_query).fetchall():
        table_fingerprint = cast(str, row[0])
        source_metadata = json.loads(cast(str, row[5]))
        source_rows.setdefault(table_fingerprint, []).append(
            {
                "source_path": cast(str, row[1]),
                "source_format": cast(str, row[2]),
                "source_sheet_name": cast(str, row[3]),
                "source_table_name": cast(str, row[4]),
                "source_metadata": source_metadata,
                "fingerprint": table_fingerprint,
            }
        )
    return content_rows, source_rows


def _has_catalog(connection: sqlite3.Connection) -> bool:
    """Return whether the shared tabular catalog exists in this database."""
    object_names = {
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
    return SQLITE_CONTENTS_TABLE in object_names and SQLITE_SOURCES_TABLE in object_names


def classify_sql_artifact(
    name: str,
    content_table_names: set[str] | None = None,
) -> str:
    """Classify a SQLite artifact using current naming conventions."""
    if name in (SQLITE_CONTENTS_TABLE, SQLITE_SOURCES_TABLE) or name in ARTIFACT_RELATION_TABLES:
        return "internal_catalog"
    content_tables = content_table_names or set()
    if name in content_tables:
        return "raw_content_table"
    if name.endswith("_typed") and name.removesuffix("_typed") in content_tables:
        return "typed_content_view"
    return "view_or_table"


def _artifact_source_paths(
    *,
    name: str,
    kind: str,
    content_rows: dict[str, CatalogContentMetadata],
    source_rows: dict[str, list[dict[str, Any]]],
) -> tuple[
    CatalogContentMetadata | None,
    list[dict[str, Any]],
    list[str],
]:
    """Return catalog metadata, source mappings, and source paths for one artifact."""
    base_table_name = name.removesuffix("_typed") if kind == "typed_content_view" else name
    content_metadata = content_rows.get(base_table_name)
    if content_metadata is None:
        return None, [], []
    source_mappings = _dedupe_source_mappings(source_rows.get(content_metadata.fingerprint, []))
    if not source_mappings:
        raise CatalogMetadataError(f"Source metadata is missing for queryable artifact `{base_table_name}`.")
    source_paths = source_paths_from_mappings(source_mappings)
    return content_metadata, source_mappings, source_paths


def _artifact_columns(
    connection: sqlite3.Connection,
    name: str,
) -> list[dict[str, Any]]:
    """Return SQLite column metadata for one table or view."""
    return [
        {
            "name": cast(str, row[1]),
            "type": cast(str, row[2]),
            "not_null": bool(row[3]),
            "default_value": row[4],
            "primary_key_position": cast(int, row[5]),
        }
        for row in connection.execute(f"PRAGMA table_info({quote_identifier(name)})").fetchall()
    ]


def source_paths_from_mappings(source_mappings: list[dict[str, Any]]) -> list[str]:
    """Return non-empty source paths while preserving first-seen order."""
    return list(dict.fromkeys(source_path for mapping in source_mappings if (source_path := str(mapping.get("source_path") or "").strip())))


def path_match_reason(
    source_path: str,
    requested_source: str,
) -> str | None:
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
    artifact_info: SqlArtifactInfo,
    artifacts_by_name: dict[str, SqlArtifactInfo],
    *,
    visited: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Return direct source mappings or inherit mappings from upstream SQL artifacts."""
    direct_mappings = _dedupe_source_mappings(artifact_info.source_mappings)
    if direct_mappings:
        return direct_mappings

    visited_artifact_names = set() if visited is None else visited
    if artifact_info.name in visited_artifact_names:
        return []
    visited_artifact_names.add(artifact_info.name)

    source_mappings: list[dict[str, Any]] = []
    for referenced_name in artifact_info.source_sql_artifact_names:
        referenced_artifact = artifacts_by_name.get(referenced_name)
        if referenced_artifact is None:
            continue
        source_mappings.extend(
            _lineage_source_mappings(
                referenced_artifact,
                artifacts_by_name,
                visited=visited_artifact_names,
            )
        )
    return _dedupe_source_mappings(source_mappings)


def _attach_reference_lineage(
    artifacts: list[SqlArtifactInfo],
    artifacts_by_name: dict[str, SqlArtifactInfo],
) -> DatabaseCatalog:
    """Return a catalog snapshot with SQL references and inherited source mappings."""
    available_artifact_names = set(artifacts_by_name)
    referenced_artifacts: list[SqlArtifactInfo] = []
    for artifact_info in artifacts:
        create_sql = artifact_info.create_sql
        source_artifact_names = (
            []
            if create_sql is None
            else referenced_artifact_names(
                create_sql,
                available_artifact_names=available_artifact_names,
                current_artifact_name=artifact_info.name,
            )
        )
        referenced_artifacts.append(
            replace(
                artifact_info,
                source_sql_artifact_names=source_artifact_names,
            )
        )

    referenced_artifacts_by_name = {artifact.name: artifact for artifact in referenced_artifacts}
    lineage_artifacts: list[SqlArtifactInfo] = []
    for artifact_info in referenced_artifacts:
        lineage_source_mappings = _lineage_source_mappings(
            artifact_info,
            referenced_artifacts_by_name,
        )
        lineage_artifacts.append(
            replace(
                artifact_info,
                source_mappings=lineage_source_mappings,
                source_paths=source_paths_from_mappings(lineage_source_mappings),
            )
        )
    return DatabaseCatalog(
        sql_artifacts=lineage_artifacts,
        sql_artifacts_by_name={artifact.name: artifact for artifact in lineage_artifacts},
    )


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
) -> DatabaseCatalog:
    """Load one cached SQLite catalog snapshot."""
    del mtime_ns, size_bytes
    database_path = Path(resolved_path)
    with closing(open_read_only_connection(database_path)) as connection:
        has_catalog = _has_catalog(connection)
        content_rows, source_rows = _fetch_catalog_metadata(connection) if has_catalog else ({}, {})
        content_table_names = set(content_rows)
        artifacts = []
        artifacts_by_name: dict[str, SqlArtifactInfo] = {}
        for master_row in connection.execute(_SQL_ARTIFACT_MASTER_SQL).fetchall():
            name = cast(str, master_row[0])
            sqlite_type = cast(str, master_row[1])
            create_sql = cast(str | None, master_row[2])
            kind = classify_sql_artifact(name, content_table_names=content_table_names)
            content_metadata, source_mappings, source_paths = _artifact_source_paths(
                name=name,
                kind=kind,
                content_rows=content_rows,
                source_rows=source_rows,
            )
            row_count = None if content_metadata is None else content_metadata.row_count
            if row_count is None:
                row_count = _artifact_row_count(connection, name)
            artifact_info = SqlArtifactInfo(
                name=name,
                sqlite_type=sqlite_type,
                kind=kind,
                create_sql=create_sql,
                columns=_artifact_columns(connection, name),
                fingerprint=None if content_metadata is None else content_metadata.fingerprint,
                content_schema=None if content_metadata is None else content_metadata.content_schema,
                row_count=row_count,
                source_mappings=source_mappings,
                source_paths=source_paths,
            )
            artifacts.append(artifact_info)
            artifacts_by_name[name] = artifact_info

        return _attach_reference_lineage(artifacts, artifacts_by_name)


def database_catalog(database_path: Path) -> DatabaseCatalog:
    """Return the cached database catalog for one SQLite file."""
    return _cached_database_catalog(
        *_database_cache_key(database_path),
    )
