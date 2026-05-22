"""Artifact catalog metadata, listing, description, and suggestions."""

from __future__ import annotations

from contextlib import closing
from pathlib import Path
import re
import sqlite3
from typing import Any, cast

from ..tabular.storage import quote_identifier
from .catalog_metadata import (
    CatalogMetadataError,
    database_catalog,
    path_match_reason,
    source_paths_from_mappings,
)
from .database import (
    error_result,
    jsonable_value,
    normalized_column_names,
    open_read_only_connection,
    requested_database_path,
    resolve_db_path,
    zip_exact,
)

MAX_DESCRIBE_SAMPLE_ROWS = 20
MAX_TEXT_VALUE_HINTS = 5
MAX_SUGGESTED_SQL_ARTIFACTS = 5
DEFAULT_CLI_ARTIFACT_LIST_LIMIT = 20
SQL_ARTIFACT_LIST_DETAILS = {"compact", "full"}
MAX_SOURCE_PATH_PREVIEW = 3
MAX_COLUMN_PREVIEW = 8
MAX_REASON_PREVIEW = 3
MAX_SOURCE_MATCH_PREVIEW = 12
_TEXT_TYPE_MARKERS = ("CHAR", "CLOB", "TEXT", "VARCHAR")
_TEXT_HINT_NAME_MARKERS = ("category", "code", "description", "group", "id", "identifier", "key", "kind", "label", "name", "segment", "status", "type")
_SUGGESTION_STOP_WORDS = {"a", "an", "and", "by", "for", "from", "how", "in", "is", "me", "of", "on", "show", "the", "to", "what", "which", "with"}


def _preview_list(
    items: list[Any],
    *,
    max_items: int,
) -> tuple[list[Any], bool]:
    """Return a bounded list preview plus truncation state."""
    safe_max_items = max(0, max_items)
    return items[:safe_max_items], len(items) > safe_max_items


def _artifact_summary(
    *,
    name: str,
    sqlite_type: str,
    kind: str,
    row_count: int | None,
    column_names: list[str],
    source_paths: list[str],
    reasons: list[str] | None = None,
) -> str:
    """Build one compact summary for a sql_artifact suggestion or listing."""
    summary_parts = [f"{name} ({kind}, {sqlite_type})", f"{len(column_names)} column(s)"]
    if row_count is not None:
        summary_parts.append(f"{row_count} row(s)")
    if source_paths:
        summary_parts.append(f"{len(source_paths)} source file(s)")
    if reasons:
        summary_parts.append("matched " + ", ".join(reasons[:MAX_REASON_PREVIEW]))
    return "; ".join(summary_parts)


def _artifact_size_label(
    *,
    row_count: int | None,
    column_count: int,
) -> str:
    """Return a compact table shape label for UI navigation."""
    row_label = "?" if row_count is None else str(row_count)
    return f"{row_label} x {column_count}"


def _sample_rows(
    connection: sqlite3.Connection,
    *,
    artifact_name: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch a small row preview for one table or view."""
    safe_limit = max(0, min(limit, MAX_DESCRIBE_SAMPLE_ROWS))
    if safe_limit == 0:
        return []

    cursor = connection.execute(
        f"SELECT * FROM {quote_identifier(artifact_name)} LIMIT ?",
        [safe_limit],
    )
    description = cursor.description or []
    column_names, _ = normalized_column_names([cast(Any, column[0]) for column in description])
    preview_rows = []
    for row in cursor.fetchall():
        preview_rows.append({column_name: jsonable_value(value) for column_name, value in zip_exact(column_names, row)})
    return preview_rows


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
    artifact_name: str,
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
            FROM {quote_identifier(artifact_name)}
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


def _artifact_search_text(
    *,
    name: str,
    sqlite_type: str,
    kind: str,
    column_names: list[str],
    source_paths: list[str],
    create_sql: str | None,
) -> str:
    """Build a search blob for one sql_artifact."""
    parts = [
        name.replace("_", " "),
        sqlite_type,
        kind.replace("_", " "),
        " ".join(column_names),
        " ".join(source_paths),
        create_sql or "",
    ]
    return " ".join(parts).lower()


def _artifact_score(
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


def _compact_artifact_listing(artifact_listing: dict[str, Any]) -> dict[str, Any]:
    """Return the compact artifact listing shape."""
    return {
        "name": artifact_listing["name"],
        "type": artifact_listing["type"],
        "kind": artifact_listing["kind"],
        "row_count": artifact_listing["row_count"],
        "column_count": artifact_listing["column_count"],
        "size_label": artifact_listing["size_label"],
        "source_path_count": artifact_listing["source_path_count"],
        "source_path_preview": artifact_listing["source_path_preview"],
        "source_paths_truncated": artifact_listing["source_paths_truncated"],
        "source_sql_artifact_names": artifact_listing["source_sql_artifact_names"],
        "summary": artifact_listing["summary"],
    }


def _visible_artifacts(catalog: dict[str, Any], *, include_internal: bool) -> list[dict[str, Any]]:
    """Return artifacts visible for a public list, source, or suggestion call."""
    artifacts = cast(list[dict[str, Any]], catalog["sql_artifacts"])
    if include_internal:
        return artifacts
    return [artifact for artifact in artifacts if artifact["kind"] != "internal_catalog"]


def _artifact_listing(artifact: dict[str, Any]) -> dict[str, Any]:
    """Return the full artifact listing shape."""
    kind = cast(str, artifact["kind"])
    source_paths = cast(list[str], artifact["source_paths"])
    source_mappings = cast(list[dict[str, Any]], artifact["source_mappings"])
    source_path_preview, source_paths_truncated = _preview_list(source_paths, max_items=MAX_SOURCE_PATH_PREVIEW)
    columns = cast(list[dict[str, Any]], artifact["columns"])
    column_names = [cast(str, column["name"]) for column in columns]
    column_preview, columns_truncated = _preview_list(columns, max_items=MAX_COLUMN_PREVIEW)
    row_count = cast(int | None, artifact["row_count"])
    column_count = len(column_names)
    return {
        "name": artifact["name"],
        "type": artifact["type"],
        "kind": kind,
        "row_count": row_count,
        "column_count": column_count,
        "column_preview": column_preview,
        "columns_truncated": columns_truncated,
        "size_label": _artifact_size_label(row_count=row_count, column_count=column_count),
        "source_mappings": source_mappings,
        "source_path_count": len(source_paths),
        "source_path_preview": source_path_preview,
        "source_paths_truncated": source_paths_truncated,
        "source_sql_artifact_names": artifact["source_sql_artifact_names"],
        "summary": _artifact_summary(
            name=cast(str, artifact["name"]),
            sqlite_type=cast(str, artifact["type"]),
            kind=kind,
            row_count=row_count,
            column_names=column_names,
            source_paths=source_paths,
        ),
    }


def _artifact_suggestion(
    artifact: dict[str, Any],
    tokens: list[str],
) -> dict[str, Any] | None:
    """Return one suggestion payload when an artifact matches a token query."""
    name = cast(str, artifact["name"])
    sqlite_type = cast(str, artifact["type"])
    kind = cast(str, artifact["kind"])
    columns = cast(list[dict[str, Any]], artifact["columns"])
    column_names = [cast(str, column["name"]) for column in columns]
    source_paths = cast(list[str], artifact["source_paths"])
    search_text = _artifact_search_text(
        name=name,
        sqlite_type=sqlite_type,
        kind=kind,
        column_names=column_names,
        source_paths=source_paths,
        create_sql=cast(str | None, artifact["create_sql"]),
    )
    score, reasons = _artifact_score(
        tokens=tokens,
        name=name,
        column_names=column_names,
        source_paths=source_paths,
        search_text=search_text,
    )
    if score <= 0:
        return None

    score += _kind_bias(kind)
    column_preview, columns_truncated = _preview_list(column_names, max_items=MAX_COLUMN_PREVIEW)
    source_path_preview, source_paths_truncated = _preview_list(source_paths, max_items=MAX_SOURCE_PATH_PREVIEW)
    return {
        "name": name,
        "type": sqlite_type,
        "kind": kind,
        "score": score,
        "reasons": reasons[:MAX_REASON_PREVIEW],
        "column_count": len(column_names),
        "column_preview": column_preview,
        "columns_truncated": columns_truncated,
        "source_path_count": len(source_paths),
        "source_path_preview": source_path_preview,
        "source_paths_truncated": source_paths_truncated,
        "row_count": artifact["row_count"],
        "summary": _artifact_summary(
            name=name,
            sqlite_type=sqlite_type,
            kind=kind,
            row_count=cast(int | None, artifact["row_count"]),
            column_names=column_names,
            source_paths=source_paths,
            reasons=reasons,
        ),
    }


def _matched_source_mappings(
    artifact: dict[str, Any],
    *,
    requested_source: str,
    requested_source_format: str,
) -> list[dict[str, Any]]:
    """Return source mappings that match the requested source path and optional format."""
    matched_mappings = []
    for mapping in cast(list[dict[str, Any]], artifact["source_mappings"]):
        if requested_source_format and str(mapping.get("source_format") or "") != requested_source_format:
            continue
        stored_source_path = str(mapping.get("source_path") or "")
        match_reason = path_match_reason(stored_source_path, requested_source)
        if match_reason is not None:
            matched_mappings.append({**mapping, "match_reason": match_reason})
    return matched_mappings


def _source_match_artifact(artifact: dict[str, Any], matched_mappings: list[dict[str, Any]]) -> dict[str, Any]:
    """Return one source-match artifact payload."""
    artifact_name = cast(str, artifact["name"])
    kind = cast(str, artifact["kind"])
    columns = cast(list[dict[str, Any]], artifact["columns"])
    column_names = [cast(str, column["name"]) for column in columns]
    column_preview, columns_truncated = _preview_list(column_names, max_items=MAX_COLUMN_PREVIEW)
    row_count = cast(int | None, artifact["row_count"])
    return {
        "name": artifact_name,
        "type": artifact["type"],
        "kind": kind,
        "row_count": row_count,
        "column_count": len(column_names),
        "column_preview": column_preview,
        "columns_truncated": columns_truncated,
        "size_label": _artifact_size_label(row_count=row_count, column_count=len(column_names)),
        "query_hint": {
            "sql_artifact_name": artifact_name,
            "select_preview_sql": f"SELECT * FROM {quote_identifier(artifact_name)} LIMIT 20;",
        },
        "source_mappings": matched_mappings,
        "source_sql_artifact_names": artifact["source_sql_artifact_names"],
        "summary": _artifact_summary(
            name=artifact_name,
            sqlite_type=cast(str, artifact["type"]),
            kind=kind,
            row_count=row_count,
            column_names=column_names,
            source_paths=source_paths_from_mappings(matched_mappings),
        ),
    }


def list_sql_artifacts(
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    include_internal: bool = False,
    max_items: int | None = None,
    detail: str = "full",
) -> dict[str, Any]:
    """List queryable SQLite tables and views."""
    requested_path = requested_database_path(root_dir=root_dir, database_path=database_path)
    try:
        if detail not in SQL_ARTIFACT_LIST_DETAILS:
            raise ValueError(f"Artifact list detail must be one of: {', '.join(sorted(SQL_ARTIFACT_LIST_DETAILS))}.")

        resolved_path = resolve_db_path(
            root_dir=root_dir,
            database_path=database_path,
        )
        catalog = database_catalog(resolved_path)
        artifact_listings = [_artifact_listing(artifact) for artifact in _visible_artifacts(catalog, include_internal=include_internal)]

        total_count = len(artifact_listings)
        if max_items is not None:
            artifact_listings = artifact_listings[: max(0, max_items)]
        truncated = len(artifact_listings) < total_count
        listed_count = len(artifact_listings)
        if detail == "compact":
            artifact_listings = [_compact_artifact_listing(artifact_listing) for artifact_listing in artifact_listings]
        summary = f"Listed {listed_count} queryable artifact(s)."
        if truncated:
            summary = f"Listed {listed_count} of {total_count} queryable artifact(s)."

        return {
            "database_path": str(resolved_path),
            "status": "ok",
            "has_tabular_catalog": catalog["has_catalog"],
            "sql_artifact_count": listed_count,
            "sql_artifact_total_count": total_count,
            "sql_artifacts_truncated": truncated,
            "detail": detail,
            "sql_artifacts": artifact_listings,
            "summary": summary,
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
    sample_rows: int = 10,
    text_value_hints: int = 3,
) -> dict[str, Any]:
    """Describe a single SQLite table or view."""
    requested_path = requested_database_path(root_dir=root_dir, database_path=database_path)
    try:
        resolved_path = resolve_db_path(
            root_dir=root_dir,
            database_path=database_path,
        )
        catalog = database_catalog(resolved_path)
        artifact_info = cast(dict[str, Any] | None, catalog["sql_artifacts_by_name"].get(sql_artifact_name))
        if artifact_info is None:
            return error_result(
                database_path=resolved_path,
                error_type="missing_sql_artifact",
                message=f"SQLite artifact does not exist: {sql_artifact_name}",
                sql_artifact_name=sql_artifact_name,
            )

        name = cast(str, artifact_info["name"])
        sqlite_type = cast(str, artifact_info["type"])
        kind = cast(str, artifact_info["kind"])
        columns = cast(list[dict[str, Any]], artifact_info["columns"])
        column_names = [cast(str, column["name"]) for column in columns]
        column_count = len(columns)
        row_count = cast(int | None, artifact_info["row_count"])
        source_mappings = cast(list[dict[str, Any]], artifact_info["source_mappings"])
        source_paths = cast(list[str], artifact_info["source_paths"])
        with closing(open_read_only_connection(resolved_path)) as connection:
            sample_row_items = _sample_rows(
                connection,
                artifact_name=name,
                limit=sample_rows,
            )
            text_value_hint_map = _text_value_hints(
                connection,
                artifact_name=name,
                columns=columns,
                max_columns=text_value_hints,
                max_values=MAX_TEXT_VALUE_HINTS,
            )

            return {
                "database_path": str(resolved_path),
                "status": "ok",
                "has_tabular_catalog": catalog["has_catalog"],
                "name": name,
                "type": sqlite_type,
                "kind": kind,
                "row_count": row_count,
                "column_count": column_count,
                "size_label": _artifact_size_label(row_count=row_count, column_count=column_count),
                "columns": columns,
                "sample_rows": sample_row_items,
                "text_value_hints": text_value_hint_map,
                "create_sql": artifact_info["create_sql"],
                "fingerprint": artifact_info["fingerprint"],
                "content_schema": artifact_info["content_schema"],
                "source_mappings": source_mappings,
                "source_path_count": len(source_paths),
                "source_path_preview": source_paths[:MAX_SOURCE_PATH_PREVIEW],
                "source_sql_artifact_names": artifact_info["source_sql_artifact_names"],
                "summary": _artifact_summary(
                    name=name,
                    sqlite_type=sqlite_type,
                    kind=kind,
                    row_count=row_count,
                    column_names=column_names,
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
        catalog = database_catalog(resolved_path)
        suggestions = []
        for artifact in _visible_artifacts(catalog, include_internal=include_internal):
            suggestion = _artifact_suggestion(artifact, tokens)
            if suggestion is not None:
                suggestions.append(suggestion)

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


def artifacts_from_source(
    source_path: str,
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    include_internal: bool = False,
    source_format: str | None = None,
) -> dict[str, Any]:
    """List queryable artifacts produced from one source path."""
    requested_path = requested_database_path(root_dir=root_dir, database_path=database_path)
    requested_source = source_path.strip()
    requested_source_format = "" if source_format is None else source_format.strip()
    try:
        if not requested_source:
            return error_result(
                database_path=requested_path,
                error_type="empty_source_path",
                message="Source path must not be empty.",
                source_path=source_path,
            )

        resolved_path = resolve_db_path(
            root_dir=root_dir,
            database_path=database_path,
        )
        catalog = database_catalog(resolved_path)
        source_matches = []
        for artifact in _visible_artifacts(catalog, include_internal=include_internal):
            matched_mappings = _matched_source_mappings(
                artifact,
                requested_source=requested_source,
                requested_source_format=requested_source_format,
            )
            if not matched_mappings:
                continue

            source_matches.append(_source_match_artifact(artifact, matched_mappings))

        source_matches.sort(key=lambda item: (cast(str, item["kind"]) != "typed_content_view", cast(str, item["name"])))
        source_match_preview, source_matches_truncated = _preview_list(source_matches, max_items=MAX_SOURCE_MATCH_PREVIEW)
        preferred_artifact = source_matches[0] if source_matches else None
        return {
            "database_path": str(resolved_path),
            "status": "ok",
            "has_tabular_catalog": catalog["has_catalog"],
            "source_path": source_path,
            "source_format": source_format,
            "artifact_count": len(source_matches),
            "preferred_artifact": preferred_artifact,
            "artifacts": source_match_preview,
            "artifacts_truncated": source_matches_truncated,
            "summary": f"Found {len(source_matches)} artifact(s) for source `{source_path}`.",
        }
    except ValueError as exc:
        return error_result(
            database_path=requested_path,
            error_type="missing_database",
            message=str(exc),
            source_path=source_path,
            source_format=source_format,
        )
    except CatalogMetadataError as exc:
        return error_result(
            database_path=requested_path,
            error_type="catalog_metadata_error",
            message=str(exc),
            source_path=source_path,
            source_format=source_format,
        )
    except (sqlite3.Error, sqlite3.Warning) as exc:
        return error_result(
            database_path=requested_path,
            error_type="sql_execution_error",
            message=str(exc),
            source_path=source_path,
            source_format=source_format,
        )
