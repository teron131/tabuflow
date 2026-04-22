"""Compact payload shaping helpers for the tabular workflow."""

from __future__ import annotations

from typing import Any

MAX_EXTRACTED_TARGET_PREVIEW = 8
MAX_TRACE_PREVIEW = 8
MAX_REPAIR_HINT_PREVIEW = 3
MAX_VALIDATION_INSTRUCTION_PREVIEW = 4
MAX_TARGET_NAME_PREVIEW = 4
MAX_SOURCE_FILE_PREVIEW = 3


def _preview_list(items: list[Any], *, max_items: int) -> tuple[list[Any], bool]:
    """Return a bounded preview of one list plus truncation state."""
    safe_max_items = max(0, max_items)
    return items[:safe_max_items], len(items) > safe_max_items


def compact_extracted_targets(targets: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a bounded preview of extracted targets for prompts and logs."""
    preview, truncated = _preview_list(targets, max_items=MAX_EXTRACTED_TARGET_PREVIEW)
    return {
        "count": len(targets),
        "truncated": truncated,
        "items": preview,
    }


def compact_sql_result(result: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return the compact SQL result payload stored by the SQL agent."""
    if result is None:
        return None
    return result


def compact_sql_agent_output(output: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a concise SQL-agent payload for workflow state."""
    if output is None:
        return None

    trace = [str(item) for item in output.get("trace", [])]
    trace_preview, trace_truncated = _preview_list(trace, max_items=MAX_TRACE_PREVIEW)
    repair_hints = list(output.get("repair_hints", []))
    repair_hint_preview, repair_hints_truncated = _preview_list(repair_hints, max_items=MAX_REPAIR_HINT_PREVIEW)
    return {
        "status": output.get("status"),
        "selected_targets": output.get("selected_targets", []),
        "candidate_sql": output.get("candidate_sql"),
        "attempts": output.get("attempts", 0),
        "rationale": output.get("rationale"),
        "last_error": output.get("last_error"),
        "repair_hints": repair_hint_preview,
        "repair_hint_count": len(repair_hints),
        "repair_hints_truncated": repair_hints_truncated,
        "result": compact_sql_result(output.get("result")),
        "trace": trace_preview,
        "trace_count": len(trace),
        "trace_truncated": trace_truncated,
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
    last_error: str | None,
    validation_feedback: dict[str, Any] | None,
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
        "last_error": last_error,
        "validation_feedback": compact_validation_feedback(validation_feedback),
    }


def build_result_message_content(artifact: dict[str, Any]) -> str:
    """Render a concise, deterministic summary for a parent chain caller."""
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

    if outcome == "fulfilled":
        headline = "Tabular workflow completed successfully."
    elif outcome == "blocked":
        headline = "Tabular workflow stopped without a final answer."
    elif outcome == "failed":
        headline = "Tabular workflow failed."
    else:
        headline = f"Tabular workflow finished with status={status}."

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

    if last_error:
        lines.append(f"Error: {last_error}")
    elif completion_reason and outcome != "fulfilled":
        lines.append(f"Completion reason: {completion_reason}")

    return "\n".join(lines)
