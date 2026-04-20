"""Compact payload shaping helpers for the tabular workflow."""

from __future__ import annotations

from typing import Any

MAX_EXTRACTED_TARGET_PREVIEW = 8
MAX_TRACE_PREVIEW = 8
MAX_REPAIR_HINT_PREVIEW = 3


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


def build_answer_payload(
    *,
    task: str,
    status: str,
    source_files: list[str],
    database_path: str | None,
    extracted_targets: list[dict[str, Any]],
    selected_targets: list[str],
    candidate_sql: str | None,
    sql_result: dict[str, Any] | None,
    saved_view_name: str | None,
    last_error: str | None,
) -> dict[str, Any]:
    """Build the compact execution payload for the final answer model."""
    return {
        "task": task,
        "status": status,
        "source_files": source_files,
        "database_path": database_path,
        "extracted_targets": compact_extracted_targets(extracted_targets),
        "selected_targets": selected_targets,
        "candidate_sql": candidate_sql,
        "sql_result": compact_sql_result(sql_result),
        "saved_view_name": saved_view_name,
        "last_error": last_error,
    }
