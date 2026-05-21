"""Artifact catalog metadata, listing, description, and suggestions."""

from __future__ import annotations

from contextlib import closing
from functools import lru_cache
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, cast

from ..tabular.storage import SQLITE_CONTENTS_TABLE, SQLITE_SOURCES_TABLE, quote_identifier
from .database import (
    error_result,
    jsonable_value,
    normalized_column_names,
    open_read_only_connection,
    requested_database_path,
    resolve_db_path,
    zip_exact,
)

MAX_DESCRIBE_SAMPLE_ROWS = 5
MAX_TEXT_VALUE_HINTS = 5
MAX_SUGGESTED_SQL_ARTIFACTS = 5
MAX_SOURCE_PATH_PREVIEW = 3
MAX_COLUMN_PREVIEW = 8
MAX_REASON_PREVIEW = 3
_TEXT_TYPE_MARKERS = ("CHAR", "CLOB", "TEXT", "VARCHAR")
_TEXT_HINT_NAME_MARKERS = ("category", "code", "description", "group", "id", "identifier", "key", "kind", "label", "name", "segment", "status", "type")
_SQL_ARTIFACT_MASTER_SQL = """
SELECT name, type, sql
FROM sqlite_master
WHERE type IN ('table', 'view')
  AND name NOT LIKE 'sqlite_%'
ORDER BY type, name
"""
_SUGGESTION_STOP_WORDS = {"a", "an", "and", "by", "for", "from", "how", "in", "is", "me", "of", "on", "show", "the", "to", "what", "which", "with"}


class CatalogMetadataError(RuntimeError):
    """Raised when a queryable catalog sql_artifact is missing required lineage."""


def _preview_list(
    items: list[Any],
    *,
    max_items: int,
) -> tuple[list[Any], bool]:
    """Return a bounded list preview plus truncation state."""
    safe_max_items = max(0, max_items)
    return items[:safe_max_items], len(items) > safe_max_items


def _sql_artifact_summary(
    *,
    name: str,
    sql_artifact_type: str,
    kind: str,
    row_count: int | None,
    column_names: list[str],
    source_paths: list[str],
    reasons: list[str] | None = None,
) -> str:
    """Build one compact summary for a sql_artifact suggestion or listing."""
    summary_parts = [f"{name} ({kind}, {sql_artifact_type})", f"{len(column_names)} column(s)"]
    if row_count is not None:
        summary_parts.append(f"{row_count} row(s)")
    if source_paths:
        summary_parts.append(f"{len(source_paths)} source file(s)")
    if reasons:
        summary_parts.append("matched " + ", ".join(reasons[:MAX_REASON_PREVIEW]))
    return "; ".join(summary_parts)


def _sql_artifact_size_label(
    *,
    row_count: int | None,
    column_count: int,
) -> str:
    """Return a compact table shape label for UI navigation."""
    row_label = "?" if row_count is None else str(row_count)
    return f"{row_label} x {column_count}"


def _sql_artifact_row_count(connection: sqlite3.Connection, sql_artifact_name: str) -> int | None:
    """Return a SQLite sql_artifact row count when catalog metadata is unavailable."""
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


def _sample_rows(
    connection: sqlite3.Connection,
    *,
    sql_artifact_name: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch a small row preview for one table or view."""
    safe_limit = max(0, min(limit, MAX_DESCRIBE_SAMPLE_ROWS))
    if safe_limit == 0:
        return []

    cursor = connection.execute(
        f"SELECT * FROM {quote_identifier(sql_artifact_name)} LIMIT ?",
        [safe_limit],
    )
    description = cursor.description or []
    column_names, _ = normalized_column_names([cast(Any, column[0]) for column in description])
    preview_rows = []
    for row in cursor.fetchall():
        preview_rows.append({column_name: jsonable_value(value) for column_name, value in zip_exact(column_names, row)})
    return preview_rows


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
    """Classify a SQLite sql_artifact using current naming conventions."""
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
    """Return catalog metadata, source mappings, and source paths for one sql_artifact."""
    content_metadata = content_rows.get(_base_table_name(name, kind))
    if content_metadata is None:
        return None, [], []
    source_mappings = _dedupe_source_mappings(source_rows.get(cast(str, content_metadata["content_id"]), []))
    if not source_mappings:
        raise CatalogMetadataError(f"Source metadata is missing for queryable sql_artifact `{content_metadata['table_name']}`.")
    source_paths = _source_paths_from_mappings(source_mappings)
    return content_metadata, source_mappings, source_paths


def _source_paths_from_mappings(source_mappings: list[dict[str, Any]]) -> list[str]:
    """Return non-empty source paths while preserving first-seen order."""
    return list(dict.fromkeys(source_path for mapping in source_mappings if (source_path := str(mapping.get("source_path") or "").strip())))


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
    """Return known SQLite SQL artifacts referenced by a stored SQL artifact."""
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
            sql_artifact_info["source_paths"] = _source_paths_from_mappings(lineage_source_mappings)
        return {
            "has_catalog": has_catalog,
            "sql_artifacts": sql_artifacts,
            "sql_artifacts_by_name": sql_artifacts_by_name,
        }


def _database_catalog(database_path: Path) -> dict[str, Any]:
    """Return the cached database catalog for one SQLite file."""
    return _cached_database_catalog(*_database_cache_key(database_path))


def _looks_like_text_column(column_name: str, declared_type: str) -> bool:
    """Return whether a column is a good candidate for distinct text-value hints."""
    normalized_type = declared_type.upper()
    normalized_name_tokens = set(re.findall(r"[a-z0-9]+", column_name.lower()))
    if any(marker in normalized_type for marker in _TEXT_TYPE_MARKERS):
        return True
    return any(marker in normalized_name_tokens for marker in _TEXT_HINT_NAME_MARKERS)


def _text_value_hints(
    connection: sqlite3.Connection,
    *,
    sql_artifact_name: str,
    columns: list[dict[str, Any]],
    max_columns: int,
    max_values: int,
) -> dict[str, list[str]]:
    """Fetch a few distinct example values for useful text-like columns."""
    hints: dict[str, list[str]] = {}
    safe_max_columns = max(0, max_columns)
    safe_max_values = max(0, min(max_values, MAX_TEXT_VALUE_HINTS))
    if safe_max_columns == 0 or safe_max_values == 0:
        return hints

    candidate_columns = [
        cast(str, column["name"])
        for column in columns
        if _looks_like_text_column(
            cast(str, column["name"]),
            cast(str, column["type"]),
        )
    ][:safe_max_columns]

    for column_name in candidate_columns:
        rows = connection.execute(
            f"""
            SELECT DISTINCT CAST({quote_identifier(column_name)} AS TEXT)
            FROM {quote_identifier(sql_artifact_name)}
            WHERE {quote_identifier(column_name)} IS NOT NULL
              AND TRIM(CAST({quote_identifier(column_name)} AS TEXT)) != ''
            LIMIT ?
            """,
            [safe_max_values],
        ).fetchall()
        values = [cast(str, row[0]) for row in rows if row and row[0] is not None]
        if values:
            hints[column_name] = values
    return hints


def _tokenize_query(text: str) -> list[str]:
    """Tokenize a natural-language query for lightweight sql_artifact suggestion."""
    tokens = [token for token in re.findall(r"[a-z0-9_]+", text.lower()) if len(token) >= 2]
    return [token for token in tokens if token not in _SUGGESTION_STOP_WORDS]


def _sql_artifact_search_text(
    *,
    name: str,
    sql_artifact_type: str,
    kind: str,
    column_names: list[str],
    source_paths: list[str],
    create_sql: str | None,
) -> str:
    """Build a search blob for one sql_artifact."""
    parts = [
        name.replace("_", " "),
        sql_artifact_type,
        kind.replace("_", " "),
        " ".join(column_names),
        " ".join(source_paths),
        create_sql or "",
    ]
    return " ".join(parts).lower()


def _sql_artifact_score(
    *,
    tokens: list[str],
    name: str,
    column_names: list[str],
    source_paths: list[str],
    search_text: str,
) -> tuple[int, list[str]]:
    """Score one sql_artifact against a lightweight NL query."""
    score = 0
    reasons: list[str] = []
    lowered_name = name.lower()
    lowered_columns = [column.lower() for column in column_names]
    lowered_sources = [source.lower() for source in source_paths]

    for token in tokens:
        if token in lowered_name:
            score += 5
            reasons.append(f"name matched '{token}'")
            continue
        matching_columns = [column for column in lowered_columns if token in column]
        if matching_columns:
            score += 3
            reasons.append(f"column matched '{token}'")
            continue
        if any(token in source for source in lowered_sources):
            score += 2
            reasons.append(f"source matched '{token}'")
            continue
        if token in search_text:
            score += 1
            reasons.append(f"context matched '{token}'")

    if tokens and all(token in lowered_name for token in tokens):
        score += 2
        reasons.append("all tokens matched sql_artifact name")
    return score, reasons


def _kind_bias(kind: str) -> int:
    """Prefer stable views over raw storage artifacts during sql_artifact suggestion."""
    if kind == "typed_content_view":
        return 3
    if kind == "view_or_table":
        return 1
    if kind == "raw_content_table":
        return -2
    if kind == "internal_catalog":
        return -5
    return 0


def list_sql_artifacts(
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    include_internal: bool = False,
) -> dict[str, Any]:
    """List queryable SQLite tables and views."""
    requested_path = requested_database_path(root_dir=root_dir, database_path=database_path)
    try:
        resolved_path = resolve_db_path(
            root_dir=root_dir,
            database_path=database_path,
        )
        catalog = _database_catalog(resolved_path)
        items = []
        for sql_artifact in cast(list[dict[str, Any]], catalog["sql_artifacts"]):
            kind = cast(str, sql_artifact["kind"])
            if not include_internal and kind == "internal_catalog":
                continue

            source_paths = cast(list[str], sql_artifact["source_paths"])
            source_mappings = cast(list[dict[str, Any]], sql_artifact["source_mappings"])
            source_path_preview, source_paths_truncated = _preview_list(source_paths, max_items=MAX_SOURCE_PATH_PREVIEW)
            columns = cast(list[dict[str, Any]], sql_artifact["columns"])
            column_names = [cast(str, column["name"]) for column in columns]
            column_preview, columns_truncated = _preview_list(columns, max_items=MAX_COLUMN_PREVIEW)
            column_count = len(column_names)
            row_count = cast(int | None, sql_artifact["row_count"])
            items.append(
                {
                    "name": sql_artifact["name"],
                    "type": sql_artifact["type"],
                    "kind": kind,
                    "row_count": row_count,
                    "column_count": column_count,
                    "column_preview": column_preview,
                    "columns_truncated": columns_truncated,
                    "size_label": _sql_artifact_size_label(row_count=row_count, column_count=column_count),
                    "source_mappings": source_mappings,
                    "source_path_count": len(source_paths),
                    "source_path_preview": source_path_preview,
                    "source_paths_truncated": source_paths_truncated,
                    "source_sql_artifact_names": sql_artifact["source_sql_artifact_names"],
                    "summary": _sql_artifact_summary(
                        name=cast(str, sql_artifact["name"]),
                        sql_artifact_type=cast(str, sql_artifact["type"]),
                        kind=kind,
                        row_count=row_count,
                        column_names=column_names,
                        source_paths=source_paths,
                    ),
                }
            )

        return {
            "database_path": str(resolved_path),
            "status": "ok",
            "has_tabular_catalog": catalog["has_catalog"],
            "sql_artifact_count": len(items),
            "sql_artifacts": items,
            "summary": f"Listed {len(items)} queryable artifact(s).",
        }
    except ValueError as exc:
        return error_result(
            database_path=requested_path,
            error_type="missing_database",
            message=str(exc),
        )
    except CatalogMetadataError as exc:
        return error_result(
            database_path=requested_path,
            error_type="catalog_metadata_error",
            message=str(exc),
        )
    except (sqlite3.Error, sqlite3.Warning) as exc:
        return error_result(
            database_path=requested_path,
            error_type="sql_execution_error",
            message=str(exc),
        )


def describe_sql_artifact(
    sql_artifact_name: str,
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    sample_rows: int = 3,
    text_value_hints: int = 3,
) -> dict[str, Any]:
    """Describe a single SQLite table or view."""
    requested_path = requested_database_path(root_dir=root_dir, database_path=database_path)
    try:
        resolved_path = resolve_db_path(
            root_dir=root_dir,
            database_path=database_path,
        )
        catalog = _database_catalog(resolved_path)
        sql_artifact_info = cast(dict[str, Any] | None, catalog["sql_artifacts_by_name"].get(sql_artifact_name))
        if sql_artifact_info is None:
            return error_result(
                database_path=resolved_path,
                error_type="missing_sql_artifact",
                message=f"SQLite artifact does not exist: {sql_artifact_name}",
                sql_artifact_name=sql_artifact_name,
            )

        name = cast(str, sql_artifact_info["name"])
        sql_artifact_type = cast(str, sql_artifact_info["type"])
        kind = cast(str, sql_artifact_info["kind"])
        columns = cast(list[dict[str, Any]], sql_artifact_info["columns"])
        column_count = len(columns)
        row_count = cast(int | None, sql_artifact_info["row_count"])
        source_mappings = cast(list[dict[str, Any]], sql_artifact_info["source_mappings"])
        source_paths = cast(list[str], sql_artifact_info["source_paths"])
        with closing(open_read_only_connection(resolved_path)) as connection:
            sample_row_items = _sample_rows(
                connection,
                sql_artifact_name=name,
                limit=sample_rows,
            )
            text_value_hint_map = _text_value_hints(
                connection,
                sql_artifact_name=name,
                columns=columns,
                max_columns=text_value_hints,
                max_values=MAX_TEXT_VALUE_HINTS,
            )

            return {
                "database_path": str(resolved_path),
                "status": "ok",
                "has_tabular_catalog": catalog["has_catalog"],
                "name": name,
                "type": sql_artifact_type,
                "kind": kind,
                "row_count": row_count,
                "column_count": column_count,
                "size_label": _sql_artifact_size_label(row_count=row_count, column_count=column_count),
                "columns": columns,
                "sample_rows": sample_row_items,
                "text_value_hints": text_value_hint_map,
                "create_sql": sql_artifact_info["create_sql"],
                "content_id": sql_artifact_info["content_id"],
                "content_schema": sql_artifact_info["content_schema"],
                "source_mappings": source_mappings,
                "source_path_count": len(source_paths),
                "source_path_preview": source_paths[:MAX_SOURCE_PATH_PREVIEW],
                "source_sql_artifact_names": sql_artifact_info["source_sql_artifact_names"],
                "summary": _sql_artifact_summary(
                    name=name,
                    sql_artifact_type=sql_artifact_type,
                    kind=kind,
                    row_count=row_count,
                    column_names=[cast(str, column["name"]) for column in columns],
                    source_paths=source_paths,
                ),
            }
    except ValueError as exc:
        return error_result(
            database_path=requested_path,
            error_type="missing_database",
            message=str(exc),
            sql_artifact_name=sql_artifact_name,
        )
    except CatalogMetadataError as exc:
        return error_result(
            database_path=requested_path,
            error_type="catalog_metadata_error",
            message=str(exc),
            sql_artifact_name=sql_artifact_name,
        )
    except (sqlite3.Error, sqlite3.Warning) as exc:
        return error_result(
            database_path=requested_path,
            error_type="sql_execution_error",
            message=str(exc),
            sql_artifact_name=sql_artifact_name,
        )


def suggest_sql_artifacts(
    question: str,
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    include_internal: bool = False,
    max_results: int = MAX_SUGGESTED_SQL_ARTIFACTS,
) -> dict[str, Any]:
    """Suggest likely tables or views for a natural-language question."""
    requested_path = requested_database_path(root_dir=root_dir, database_path=database_path)
    safe_max_results = max(1, max_results)
    try:
        tokens = _tokenize_query(question)
        if not tokens:
            return error_result(
                database_path=requested_path,
                error_type="empty_question",
                message="Question must include at least one meaningful search token.",
                max_results=safe_max_results,
            )

        resolved_path = resolve_db_path(
            root_dir=root_dir,
            database_path=database_path,
        )
        catalog = _database_catalog(resolved_path)
        suggestions = []
        for sql_artifact in cast(list[dict[str, Any]], catalog["sql_artifacts"]):
            name = cast(str, sql_artifact["name"])
            sql_artifact_type = cast(str, sql_artifact["type"])
            kind = cast(str, sql_artifact["kind"])
            if not include_internal and kind == "internal_catalog":
                continue

            column_names = [cast(str, column["name"]) for column in cast(list[dict[str, Any]], sql_artifact["columns"])]
            source_paths = cast(list[str], sql_artifact["source_paths"])
            search_text = _sql_artifact_search_text(
                name=name,
                sql_artifact_type=sql_artifact_type,
                kind=kind,
                column_names=column_names,
                source_paths=source_paths,
                create_sql=cast(str | None, sql_artifact["create_sql"]),
            )
            score, reasons = _sql_artifact_score(
                tokens=tokens,
                name=name,
                column_names=column_names,
                source_paths=source_paths,
                search_text=search_text,
            )
            if score <= 0:
                continue
            score += _kind_bias(kind)
            column_preview, columns_truncated = _preview_list(column_names, max_items=MAX_COLUMN_PREVIEW)
            source_path_preview, source_paths_truncated = _preview_list(source_paths, max_items=MAX_SOURCE_PATH_PREVIEW)

            suggestions.append(
                {
                    "name": name,
                    "type": sql_artifact_type,
                    "kind": kind,
                    "score": score,
                    "reasons": reasons[:MAX_REASON_PREVIEW],
                    "column_count": len(column_names),
                    "column_preview": column_preview,
                    "columns_truncated": columns_truncated,
                    "source_path_count": len(source_paths),
                    "source_path_preview": source_path_preview,
                    "source_paths_truncated": source_paths_truncated,
                    "row_count": sql_artifact["row_count"],
                    "summary": _sql_artifact_summary(
                        name=name,
                        sql_artifact_type=sql_artifact_type,
                        kind=kind,
                        row_count=cast(int | None, sql_artifact["row_count"]),
                        column_names=column_names,
                        source_paths=source_paths,
                        reasons=reasons,
                    ),
                }
            )

        suggestions.sort(key=lambda item: (-cast(int, item["score"]), cast(str, item["name"])))
        top_suggestions = suggestions[:safe_max_results]
        return {
            "database_path": str(resolved_path),
            "status": "ok",
            "has_tabular_catalog": catalog["has_catalog"],
            "question": question,
            "tokens": tokens,
            "suggestion_count": len(top_suggestions),
            "suggestions": top_suggestions,
            "summary": f"Suggested {len(top_suggestions)} artifact(s) for {len(tokens)} search token(s).",
        }
    except ValueError as exc:
        return error_result(
            database_path=requested_path,
            error_type="missing_database",
            message=str(exc),
            question=question,
            max_results=safe_max_results,
        )
    except CatalogMetadataError as exc:
        return error_result(
            database_path=requested_path,
            error_type="catalog_metadata_error",
            message=str(exc),
            question=question,
            max_results=safe_max_results,
        )
    except (sqlite3.Error, sqlite3.Warning) as exc:
        return error_result(
            database_path=requested_path,
            error_type="sql_execution_error",
            message=str(exc),
            question=question,
            max_results=safe_max_results,
        )


def list_artifacts(
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    include_internal: bool = False,
) -> dict[str, Any]:
    """List queryable artifacts in the SQLite cache."""
    return list_sql_artifacts(
        root_dir=root_dir,
        database_path=database_path,
        include_internal=include_internal,
    )


def describe_artifact(
    name: str,
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    sample_rows: int = 3,
    text_value_hints: int = 3,
) -> dict[str, Any]:
    """Describe one queryable artifact in the SQLite cache."""
    return describe_sql_artifact(
        name,
        root_dir=root_dir,
        database_path=database_path,
        sample_rows=sample_rows,
        text_value_hints=text_value_hints,
    )
