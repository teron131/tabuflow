"""Payload builders for artifact catalog listings, suggestions, and source matches."""

from __future__ import annotations

import re
from typing import Any, cast

from ...workspace_db import quote_identifier
from .metadata import path_match_reason, source_paths_from_mappings

MAX_SOURCE_PATH_PREVIEW = 3
MAX_COLUMN_PREVIEW = 8
MAX_REASON_PREVIEW = 3
MAX_SOURCE_MATCH_PREVIEW = 12
SUGGESTION_STOP_WORDS = {"a", "an", "and", "by", "for", "from", "how", "in", "is", "me", "of", "on", "show", "the", "to", "what", "which", "with"}


def preview_items(
    items: list[Any],
    *,
    max_items: int,
) -> tuple[list[Any], bool]:
    """Return a bounded list preview plus truncation state."""
    safe_max_items = max(0, max_items)
    return items[:safe_max_items], len(items) > safe_max_items


def sql_artifact_summary(
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


def sql_artifact_size_label(
    *,
    row_count: int | None,
    column_count: int,
) -> str:
    """Return a compact table shape label for UI navigation."""
    row_label = "?" if row_count is None else str(row_count)
    return f"{row_label} x {column_count}"


def tokenize_query(text: str) -> list[str]:
    """Tokenize a natural-language query for lightweight sql_artifact suggestion."""
    tokens = [token for token in re.findall(r"[a-z0-9_]+", text.lower()) if len(token) >= 2]
    return [token for token in tokens if token not in SUGGESTION_STOP_WORDS]


def sql_artifact_search_text(
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


def sql_artifact_score(
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


def sql_artifact_kind_bias(kind: str) -> int:
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


def compact_sql_artifact_listing(artifact_listing: dict[str, Any]) -> dict[str, Any]:
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
        "summary": artifact_listing["summary"],
    }


def visible_sql_artifacts(
    catalog: dict[str, Any],
    *,
    include_internal: bool,
) -> list[dict[str, Any]]:
    """Return artifacts visible for a public list, source, or suggestion call."""
    artifacts = cast(list[dict[str, Any]], catalog["sql_artifacts"])
    if include_internal:
        return artifacts
    return [artifact for artifact in artifacts if artifact["kind"] != "internal_catalog"]


def sql_artifact_listing(artifact: dict[str, Any]) -> dict[str, Any]:
    """Return the full artifact listing shape."""
    kind = cast(str, artifact["kind"])
    source_paths = cast(list[str], artifact["source_paths"])
    source_mappings = cast(list[dict[str, Any]], artifact["source_mappings"])
    source_path_preview, source_paths_truncated = preview_items(source_paths, max_items=MAX_SOURCE_PATH_PREVIEW)
    columns = cast(list[dict[str, Any]], artifact["columns"])
    column_names = [cast(str, column["name"]) for column in columns]
    column_preview, columns_truncated = preview_items(columns, max_items=MAX_COLUMN_PREVIEW)
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
        "size_label": sql_artifact_size_label(row_count=row_count, column_count=column_count),
        "source_mappings": source_mappings,
        "source_path_count": len(source_paths),
        "source_path_preview": source_path_preview,
        "source_paths_truncated": source_paths_truncated,
        "summary": sql_artifact_summary(
            name=cast(str, artifact["name"]),
            sqlite_type=cast(str, artifact["type"]),
            kind=kind,
            row_count=row_count,
            column_names=column_names,
            source_paths=source_paths,
        ),
    }


def sql_artifact_suggestion(
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
    search_text = sql_artifact_search_text(
        name=name,
        sqlite_type=sqlite_type,
        kind=kind,
        column_names=column_names,
        source_paths=source_paths,
        create_sql=cast(str | None, artifact["create_sql"]),
    )
    score, reasons = sql_artifact_score(
        tokens=tokens,
        name=name,
        column_names=column_names,
        source_paths=source_paths,
        search_text=search_text,
    )
    if score <= 0:
        return None

    score += sql_artifact_kind_bias(kind)
    column_preview, columns_truncated = preview_items(column_names, max_items=MAX_COLUMN_PREVIEW)
    source_path_preview, source_paths_truncated = preview_items(source_paths, max_items=MAX_SOURCE_PATH_PREVIEW)
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
        "summary": sql_artifact_summary(
            name=name,
            sqlite_type=sqlite_type,
            kind=kind,
            row_count=cast(int | None, artifact["row_count"]),
            column_names=column_names,
            source_paths=source_paths,
            reasons=reasons,
        ),
    }


def matched_source_artifact_mappings(
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


def source_match_sql_artifact(
    artifact: dict[str, Any],
    matched_mappings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Return one source-match artifact payload."""
    artifact_name = cast(str, artifact["name"])
    kind = cast(str, artifact["kind"])
    columns = cast(list[dict[str, Any]], artifact["columns"])
    column_names = [cast(str, column["name"]) for column in columns]
    column_preview, columns_truncated = preview_items(column_names, max_items=MAX_COLUMN_PREVIEW)
    row_count = cast(int | None, artifact["row_count"])
    return {
        "name": artifact_name,
        "type": artifact["type"],
        "kind": kind,
        "row_count": row_count,
        "column_count": len(column_names),
        "column_preview": column_preview,
        "columns_truncated": columns_truncated,
        "size_label": sql_artifact_size_label(row_count=row_count, column_count=len(column_names)),
        "query_hint": {
            "sql_artifact_name": artifact_name,
            "select_preview_sql": f"SELECT * FROM {quote_identifier(artifact_name)} LIMIT 20;",
        },
        "source_mappings": matched_mappings,
        "summary": sql_artifact_summary(
            name=artifact_name,
            sqlite_type=cast(str, artifact["type"]),
            kind=kind,
            row_count=row_count,
            column_names=column_names,
            source_paths=source_paths_from_mappings(matched_mappings),
        ),
    }
