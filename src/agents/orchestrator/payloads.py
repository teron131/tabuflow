"""Compact payload shaping helpers for orchestrator-owned workflow runs."""

from __future__ import annotations

from typing import Any

from ..prep_agent.payloads import compact_extracted_targets

MAX_TRACE_PREVIEW = 8
MAX_REPAIR_HINT_PREVIEW = 3
MAX_VALIDATION_INSTRUCTION_PREVIEW = 4
MAX_TARGET_NAME_PREVIEW = 4
MAX_SOURCE_FILE_PREVIEW = 3
MAX_SQL_RESULT_COLUMN_PREVIEW = 8
MAX_SQL_RESULT_ROW_PREVIEW = 2


def _preview_list(items: list[Any], *, max_items: int) -> tuple[list[Any], bool]:
    """Return a bounded preview of one list plus truncation state."""
    safe_max_items = max(0, max_items)
    return items[:safe_max_items], len(items) > safe_max_items


def compact_sql_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the compact SQL result payload stored by the SQL agent."""
    if result is None:
        return None
    if result.get("status") != "ok":
        return {
            "status": result.get("status"),
            "error_type": result.get("error_type"),
            "message": result.get("message"),
            "database_path": result.get("database_path"),
            "summary": result.get("summary"),
        }

    columns = [str(column) for column in result.get("columns", [])]
    column_preview, columns_truncated = _preview_list(columns, max_items=MAX_SQL_RESULT_COLUMN_PREVIEW)
    rows = list(result.get("rows", []))
    row_preview, rows_truncated = _preview_list(rows, max_items=MAX_SQL_RESULT_ROW_PREVIEW)

    compact_rows: list[dict[str, Any]] = []
    for row in row_preview:
        if not isinstance(row, dict):
            compact_rows.append({"value": row})
            continue
        row_items = list(row.items())
        compact_items, row_columns_truncated = _preview_list(row_items, max_items=MAX_SQL_RESULT_COLUMN_PREVIEW)
        compact_row = dict(compact_items)
        if row_columns_truncated:
            compact_row["__remaining_columns__"] = len(row_items) - len(compact_items)
        compact_rows.append(compact_row)

    return {
        "status": "ok",
        "database_path": result.get("database_path"),
        "summary": result.get("summary"),
        "row_count": result.get("row_count", len(rows)),
        "truncated": bool(result.get("truncated")),
        "column_count": len(columns),
        "columns": column_preview,
        "columns_truncated": columns_truncated,
        "rows": compact_rows,
        "rows_truncated": rows_truncated or bool(result.get("truncated")),
    }


def compact_validation_feedback(feedback: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a concise validation-feedback payload for prompts and logs."""
    if feedback is None:
        return None

    instructions = [str(item) for item in feedback.get("instructions", [])]
    preview, truncated = _preview_list(instructions, max_items=MAX_VALIDATION_INSTRUCTION_PREVIEW)
    return {
        "failure_type": feedback.get("failure_type"),
        "retryable": feedback.get("retryable", True),
        "summary": feedback.get("summary"),
        "instructions": preview,
        "instruction_count": len(instructions),
        "instructions_truncated": truncated,
        "rationale": feedback.get("rationale"),
    }


def _preview_names(items: list[str], *, max_items: int) -> str:
    """Render a compact comma-separated preview for tool-facing text."""
    preview, truncated = _preview_list(items, max_items=max_items)
    if not preview:
        return "(none)"
    suffix = f" (+{len(items) - len(preview)} more)" if truncated else ""
    return ", ".join(preview) + suffix


def build_result_artifact(
    *,
    task: str,
    status: str,
    outcome: str,
    completion_reason: str | None,
    source_files: list[str],
    database_path: str | None,
    extracted_targets: list[dict[str, Any]],
    selected_targets: list[str],
    candidate_sql: str | None,
    sql_result: dict[str, Any] | None,
    saved_view_name: str | None,
    saved_view: dict[str, Any] | None,
    last_error: str | None,
    validation_feedback: dict[str, Any] | None,
    validation_attempts: int,
    trace: list[str],
) -> dict[str, Any]:
    """Build the compact execution payload for a workflow or tool result."""
    return {
        "task": task,
        "status": status,
        "outcome": outcome,
        "completion_reason": completion_reason,
        "source_files": source_files,
        "database_path": database_path,
        "extracted_targets": compact_extracted_targets(extracted_targets),
        "selected_targets": selected_targets,
        "candidate_sql": candidate_sql,
        "sql_result": compact_sql_result(sql_result),
        "saved_view_name": saved_view_name,
        "saved_view": saved_view,
        "last_error": last_error,
        "validation_feedback": compact_validation_feedback(validation_feedback),
        "validation_attempts": validation_attempts,
        "trace": trace,
    }


def build_result_message(artifact: dict[str, Any]) -> str:
    """Render a concise, deterministic summary for a workflow caller."""
    outcome = str(artifact.get("outcome", "pending"))
    status = str(artifact.get("status", "pending"))
    task = str(artifact.get("task", "")).strip()
    source_files = [str(item) for item in artifact.get("source_files", [])]
    selected_targets = [str(item) for item in artifact.get("selected_targets", [])]
    saved_view_name = artifact.get("saved_view_name")
    sql_result = artifact.get("sql_result") or {}
    validation_feedback = artifact.get("validation_feedback") or {}
    completion_reason = artifact.get("completion_reason")
    last_error = artifact.get("last_error")
    validation_attempts = artifact.get("validation_attempts")

    if outcome == "fulfilled":
        headline = "Workflow completed successfully."
    elif outcome == "blocked":
        headline = "Workflow stopped without a final answer."
    elif outcome == "failed":
        headline = "Workflow failed."
    else:
        headline = f"Workflow finished with status={status}."

    lines = [headline]
    if task:
        lines.append(f"Task: {task}")
    lines.append(f"Source files: {_preview_names(source_files, max_items=MAX_SOURCE_FILE_PREVIEW)}")

    extracted_targets = artifact.get("extracted_targets") or {}
    target_count = int(extracted_targets.get("count", 0))
    if selected_targets:
        lines.append(f"Targets used: {_preview_names(selected_targets, max_items=MAX_TARGET_NAME_PREVIEW)}")
    elif target_count:
        lines.append(f"Prepared targets: {target_count}")

    if sql_result:
        row_count = sql_result.get("row_count")
        summary = sql_result.get("summary")
        if row_count is not None:
            lines.append(f"Result rows: {row_count}")
        if summary:
            lines.append(f"Result summary: {summary}")

    if saved_view_name:
        lines.append(f"Saved view: {saved_view_name}")

    feedback_summary = validation_feedback.get("summary")
    if feedback_summary and outcome != "fulfilled":
        lines.append(f"Validation feedback: {feedback_summary}")
    if validation_attempts:
        lines.append(f"Validation attempts: {validation_attempts}")

    if last_error:
        lines.append(f"Error: {last_error}")
    elif completion_reason and outcome != "fulfilled":
        lines.append(f"Completion reason: {completion_reason}")

    return "\n".join(lines)
