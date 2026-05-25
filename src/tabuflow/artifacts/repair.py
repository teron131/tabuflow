"""Deterministic SQLite artifact repair hints."""

from __future__ import annotations

from contextlib import closing, suppress
from difflib import SequenceMatcher
from pathlib import Path
import re
import sqlite3
from typing import Any, cast

from ..workspace_db import SQLITE_CONTENTS_TABLE, SQLITE_SOURCES_TABLE, open_read_only_connection, quote_identifier, resolve_db_path

MAX_REPAIR_CANDIDATES = 3
AMBIGUOUS_COLUMN_ERROR_PREFIX = "ambiguous column name:"
MISSING_COLUMN_ERROR_PREFIX = "no such column:"
MISSING_TABLE_ERROR_PREFIX = "no such table:"
INTERNAL_SQLITE_TABLES = {SQLITE_CONTENTS_TABLE, SQLITE_SOURCES_TABLE}
UNQUOTED_HYPHENATED_REFERENCE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w$]*(?:-[A-Za-z0-9_]+)+)\b", re.IGNORECASE)


def identifier_tokens(value: str) -> set[str]:
    """Split one identifier into comparable lowercase tokens."""
    return set(re.findall(r"[a-z0-9]+", value.lower()))


def identifier_similarity(reference: str, candidate: str) -> float:
    """Score one candidate identifier against a missing identifier."""
    normalized_reference = re.sub(r"[^a-z0-9]+", "", reference.lower())
    normalized_candidate = re.sub(r"[^a-z0-9]+", "", candidate.lower())
    if not normalized_reference or not normalized_candidate:
        return 0.0
    if normalized_reference == normalized_candidate:
        return 100.0

    score = 0.0
    if normalized_reference in normalized_candidate or normalized_candidate in normalized_reference:
        score += 40.0

    reference_tokens = identifier_tokens(reference)
    candidate_tokens = identifier_tokens(candidate)
    if reference_tokens and candidate_tokens:
        shared_tokens = reference_tokens & candidate_tokens
        score += 20.0 * len(shared_tokens)
        if shared_tokens == reference_tokens == candidate_tokens:
            score += 20.0

    score += SequenceMatcher(a=normalized_reference, b=normalized_candidate).ratio() * 20.0
    return score


def rank_identifier_candidates(
    identifier: str,
    candidates: list[str],
    *,
    max_matches: int,
) -> list[str]:
    """Return the best schema identifier matches for a missing SQL artifact or column."""
    if not identifier or max_matches <= 0:
        return []

    scored_candidates = sorted(
        ((identifier_similarity(identifier, candidate), candidate) for candidate in dict.fromkeys(candidates)),
        key=lambda item: (-item[0], item[1]),
    )
    return [candidate for score, candidate in scored_candidates if score > 0][:max_matches]


def error_identifier(error_message: str, prefix: str) -> str:
    """Extract the identifier payload from a SQLite error message."""
    identifier = error_message[len(prefix) :].strip()
    if not identifier:
        return ""
    unqualified = identifier.rsplit(".", 1)[-1]
    return unqualified.strip('"`[]')


def format_repair_candidates(
    candidates: list[dict[str, Any]],
    *,
    include_sql_artifacts: bool,
) -> str:
    """Format repair candidates into one compact human-readable string."""
    parts = []
    for candidate in candidates:
        name = cast(str, candidate["name"])
        if include_sql_artifacts:
            sql_artifacts = cast(list[str], candidate.get("sql_artifacts", []))
            sql_artifact_suffix = f" on {', '.join(sql_artifacts)}" if sql_artifacts else ""
            parts.append(f"{name}{sql_artifact_suffix}")
        else:
            parts.append(name)
    return ", ".join(parts)


def repair_result(
    *,
    kind: str,
    identifier: str,
    candidates: list[dict[str, Any]],
    message: str,
) -> list[dict[str, Any]]:
    """Wrap one repair suggestion in the stable public payload shape."""
    return [
        {
            "kind": kind,
            "identifier": identifier,
            "candidates": candidates,
            "message": message,
        }
    ]


def inspect_sql_artifact_schema(
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    include_internal: bool = False,
) -> tuple[list[str], dict[str, list[str]]]:
    """Return SQLite artifact names and columns for standalone repair hints."""
    resolved_path = resolve_db_path(root_dir=root_dir, database_path=database_path)
    artifact_columns: dict[str, list[str]] = {}
    with closing(open_read_only_connection(resolved_path)) as connection:
        rows = connection.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type IN ('table', 'view')
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
        for row in rows:
            artifact_name = cast(str, row[0])
            if not include_internal and artifact_name in INTERNAL_SQLITE_TABLES:
                continue
            columns = [cast(str, column[1]) for column in connection.execute(f"PRAGMA table_info({quote_identifier(artifact_name)})").fetchall() if str(column[1]).strip()]
            artifact_columns[artifact_name] = columns
    return list(artifact_columns), artifact_columns


def suggest_sql_error_repair_from_schema(
    error_message: str,
    *,
    available_sql_artifacts: list[str],
    sql_artifact_columns: dict[str, list[str]],
    max_matches: int = MAX_REPAIR_CANDIDATES,
) -> list[dict[str, Any]]:
    """Return deterministic schema-aware repair hints for common SQLite errors."""
    safe_max_matches = max(1, max_matches)
    lowered_error = error_message.strip().lower()

    if lowered_error.startswith(MISSING_COLUMN_ERROR_PREFIX):
        missing_column = error_identifier(error_message, MISSING_COLUMN_ERROR_PREFIX)
        columns_by_name: dict[str, list[str]] = {}
        for sql_artifact_name, columns in sql_artifact_columns.items():
            for column_name in columns:
                columns_by_name.setdefault(column_name, []).append(sql_artifact_name)

        candidate_columns = rank_identifier_candidates(
            missing_column,
            list(columns_by_name),
            max_matches=safe_max_matches,
        )
        if not candidate_columns:
            return []

        candidates = [
            {
                "name": column_name,
                "sql_artifacts": sorted(columns_by_name[column_name]),
            }
            for column_name in candidate_columns
        ]
        return repair_result(
            kind="missing_column",
            identifier=missing_column,
            candidates=candidates,
            message=f"Column `{missing_column}` was not found. Closest inspected columns: {format_repair_candidates(candidates, include_sql_artifacts=True)}.",
        )

    if lowered_error.startswith(MISSING_TABLE_ERROR_PREFIX):
        missing_sql_artifact = error_identifier(error_message, MISSING_TABLE_ERROR_PREFIX)
        candidate_sql_artifacts = rank_identifier_candidates(
            missing_sql_artifact,
            available_sql_artifacts,
            max_matches=safe_max_matches,
        )
        if not candidate_sql_artifacts:
            return []

        candidates = [{"name": sql_artifact_name} for sql_artifact_name in candidate_sql_artifacts]
        return repair_result(
            kind="missing_sql_artifact",
            identifier=missing_sql_artifact,
            candidates=candidates,
            message=f"SQL artifact `{missing_sql_artifact}` was not found. Closest inspected SQL artifacts: {format_repair_candidates(candidates, include_sql_artifacts=False)}.",
        )

    if lowered_error.startswith(AMBIGUOUS_COLUMN_ERROR_PREFIX):
        ambiguous_column = error_identifier(error_message, AMBIGUOUS_COLUMN_ERROR_PREFIX)
        matching_sql_artifacts = sorted(sql_artifact_name for sql_artifact_name, columns in sql_artifact_columns.items() if ambiguous_column in columns)
        if not matching_sql_artifacts:
            return []

        candidates = [{"name": ambiguous_column, "sql_artifacts": matching_sql_artifacts}]
        return repair_result(
            kind="ambiguous_column",
            identifier=ambiguous_column,
            candidates=candidates,
            message=f"Column `{ambiguous_column}` is ambiguous. Qualify it with one of: {', '.join(matching_sql_artifacts)}.",
        )

    return []


def suggest_sql_error_repair(
    error_message: str,
    *,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
    include_internal: bool = False,
    max_matches: int = MAX_REPAIR_CANDIDATES,
) -> list[dict[str, Any]]:
    """Return schema-aware repair hints by inspecting a SQLite artifact database."""
    available_sql_artifacts, sql_artifact_columns = inspect_sql_artifact_schema(
        root_dir=root_dir,
        database_path=database_path,
        include_internal=include_internal,
    )
    return suggest_sql_error_repair_from_schema(
        error_message,
        available_sql_artifacts=available_sql_artifacts,
        sql_artifact_columns=sql_artifact_columns,
        max_matches=max_matches,
    )


def sql_error_repair_hints(
    *,
    sql: str,
    error_message: str,
    root_dir: str | Path | None = None,
    database_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return deterministic repair hints for a failed artifact query."""
    hints: list[dict[str, Any]] = []
    referenced_hyphenated_names = list(dict.fromkeys(UNQUOTED_HYPHENATED_REFERENCE.findall(sql)))
    if referenced_hyphenated_names:
        hints.append(
            {
                "kind": "quote_sql_artifact",
                "identifier": referenced_hyphenated_names[0],
                "candidates": [{"name": name, "quoted": quote_identifier(name)} for name in referenced_hyphenated_names],
                "message": "Quote SQL artifact names that contain hyphens, for example FROM " + quote_identifier(referenced_hyphenated_names[0]) + ".",
            }
        )

    with suppress(sqlite3.Error, ValueError, RuntimeError):
        hints.extend(
            suggest_sql_error_repair(
                error_message,
                root_dir=root_dir,
                database_path=database_path,
            )
        )
    return hints
