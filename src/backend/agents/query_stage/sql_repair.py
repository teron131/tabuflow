"""Query-stage adapters for deterministic SQL repair hints."""

from typing import Any

from tabuflow.artifacts import suggest_sql_error_repair_from_schema
from .state import QueryStageState


def preferred_sql_artifact_names(state: QueryStageState) -> list[str]:
    """Return orchestrator-provided SQL artifact names in first-seen order."""
    sql_artifact_names: list[str] = []
    seen_names: set[str] = set()
    for name in state.preferred_sql_artifacts:
        sql_artifact_name = name.strip()
        if not sql_artifact_name or sql_artifact_name in seen_names:
            continue
        seen_names.add(sql_artifact_name)
        sql_artifact_names.append(sql_artifact_name)
    for sql_artifact in state.extracted_sql_artifacts:
        sql_artifact_name = str(sql_artifact.get("typed_view_name") or sql_artifact.get("table_name") or "").strip()
        if not sql_artifact_name or sql_artifact_name in seen_names:
            continue
        seen_names.add(sql_artifact_name)
        sql_artifact_names.append(sql_artifact_name)
    return sql_artifact_names


def query_stage_sql_artifact_columns(state: QueryStageState) -> dict[str, list[str]]:
    """Return SQL artifact columns from the current query-stage graph state."""
    sql_artifact_columns: dict[str, list[str]] = {}
    for sql_artifact in state.extracted_sql_artifacts:
        sql_artifact_name = str(sql_artifact.get("typed_view_name") or sql_artifact.get("table_name") or "").strip()
        if not sql_artifact_name:
            continue
        columns = sql_artifact.get("typed_columns") or sql_artifact.get("db_columns") or sql_artifact.get("columns") or []
        sql_artifact_columns[sql_artifact_name] = [str(column) for column in columns if str(column).strip()]
    return sql_artifact_columns


def suggest_query_stage_sql_repair(
    state: QueryStageState,
    error_message: str,
) -> list[dict[str, Any]]:
    """Return deterministic repair hints scoped to the current query-stage state."""
    return suggest_sql_error_repair_from_schema(
        error_message,
        available_sql_artifacts=sorted(set(preferred_sql_artifact_names(state))),
        sql_artifact_columns=query_stage_sql_artifact_columns(state),
    )
