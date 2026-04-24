"""Prompt payload shaping for the SQL planning agent."""

from __future__ import annotations

import json
from typing import Any

from langchain.messages import HumanMessage

from .state import SQLAgentState

MAX_AGENT_SAMPLE_ROWS = 2
MAX_AGENT_ROW_COLUMNS = 8
MAX_AGENT_TEXT_HINT_COLUMNS = 2
MAX_AGENT_TEXT_HINT_VALUES = 3
MAX_AGENT_SOURCE_MAPPING_PREVIEW = 2
MAX_AGENT_SKILL_REF_PREVIEW = 8


def _preview_list(
    items: list[Any],
    *,
    max_items: int,
) -> tuple[
    list[Any],
    bool,
]:
    """Return a bounded preview of one list plus truncation state."""
    safe_max_items = max(0, max_items)
    return items[:safe_max_items], len(items) > safe_max_items


def _compact_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return a bounded row preview for prompts and traces."""
    row_items = list(row.items())
    preview_items, truncated = _preview_list(
        row_items,
        max_items=MAX_AGENT_ROW_COLUMNS,
    )
    compact_row = dict(preview_items)
    if truncated:
        compact_row["__remaining_columns__"] = len(row_items) - len(preview_items)
    return compact_row


def _compact_sample_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a bounded sample-row preview for one inspected target."""
    preview_rows, truncated = _preview_list(
        rows,
        max_items=MAX_AGENT_SAMPLE_ROWS,
    )
    return {
        "count": len(rows),
        "truncated": truncated,
        "items": [_compact_row(row) for row in preview_rows],
    }


def _compact_text_value_hints(text_value_hints: dict[str, Any]) -> dict[str, Any]:
    """Return bounded text-value hints for planning."""
    hint_items = list(text_value_hints.items())
    preview_items, truncated = _preview_list(
        hint_items,
        max_items=MAX_AGENT_TEXT_HINT_COLUMNS,
    )
    compact_hints = {str(column_name): [str(value) for value in values[:MAX_AGENT_TEXT_HINT_VALUES]] for column_name, values in preview_items if isinstance(values, list)}
    return {
        "count": len(hint_items),
        "truncated": truncated,
        "items": compact_hints,
    }


def _compact_source_mappings(source_mappings: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a bounded preview of source mappings."""
    preview_mappings, truncated = _preview_list(
        source_mappings,
        max_items=MAX_AGENT_SOURCE_MAPPING_PREVIEW,
    )
    return {
        "count": len(source_mappings),
        "truncated": truncated,
        "items": [
            {
                "source_path": mapping.get("source_path"),
                "source_sheet_name": mapping.get("source_sheet_name"),
                "source_table_name": mapping.get("source_table_name"),
            }
            for mapping in preview_mappings
        ],
    }


def _compact_skill_refs(skill_refs: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a bounded preview of worker-visible skill references."""
    preview_refs, truncated = _preview_list(
        skill_refs,
        max_items=MAX_AGENT_SKILL_REF_PREVIEW,
    )
    return {
        "count": len(skill_refs),
        "truncated": truncated,
        "items": [
            {
                "name": skill_ref.get("name"),
                "path": skill_ref.get("path"),
                "instructions_path": (skill_ref.get("instructions") or {}).get("relative_path"),
                "reference_count": len(skill_ref.get("references", [])) if isinstance(skill_ref.get("references"), list) else 0,
                "sql_reference_count": (
                    sum(1 for reference in skill_ref.get("references", []) if str(reference.get("kind", "")).lower() == "sql")
                    if isinstance(skill_ref.get("references"), list)
                    else 0
                ),
            }
            for skill_ref in preview_refs
        ],
    }


def _compact_inspected_target(target: dict[str, Any]) -> dict[str, Any]:
    """Return the compact inspected-target payload sent to the planner."""
    columns = list(target.get("columns", []))
    return {
        "name": target.get("name"),
        "kind": target.get("kind"),
        "type": target.get("type"),
        "row_count": target.get("row_count"),
        "summary": target.get("summary"),
        "columns": [
            {
                "name": column.get("name"),
                "type": column.get("type"),
            }
            for column in columns
        ],
        "column_count": len(columns),
        "sample_rows": _compact_sample_rows(
            list(target.get("sample_rows", [])),
        ),
        "text_value_hints": _compact_text_value_hints(dict(target.get("text_value_hints") or {})),
        "source_mappings": _compact_source_mappings(
            list(target.get("source_mappings", [])),
        ),
    }


def _planner_candidate_targets(state: SQLAgentState) -> list[dict[str, Any]]:
    """Return planner candidate targets, preferring current-run targets when available."""
    if not state.preferred_targets:
        return state.suggestions

    preferred_target_names = set(state.preferred_targets)
    suggestions_by_name = {str(suggestion["name"]): suggestion for suggestion in state.suggestions if suggestion.get("name")}

    inspected_candidates = []
    for target in state.inspected_targets:
        target_name = str(target.get("name", "")).strip()
        if not target_name or target_name not in preferred_target_names:
            continue

        suggestion = suggestions_by_name.get(target_name, {})
        column_names = [str(column.get("name")) for column in target.get("columns", []) if column.get("name")]
        source_paths = list(dict.fromkeys(str(mapping.get("source_path")) for mapping in target.get("source_mappings", []) if mapping.get("source_path")))
        column_preview, columns_truncated = _preview_list(
            column_names,
            max_items=MAX_AGENT_ROW_COLUMNS,
        )
        source_path_preview, source_paths_truncated = _preview_list(
            source_paths,
            max_items=MAX_AGENT_SOURCE_MAPPING_PREVIEW,
        )
        inspected_candidates.append(
            {
                "name": target_name,
                "type": target.get("type"),
                "kind": target.get("kind"),
                "score": suggestion.get("score", 0),
                "reasons": list(dict.fromkeys(["current run target", *list(suggestion.get("reasons", []))])),
                "column_count": len(column_names),
                "column_preview": column_preview,
                "columns_truncated": columns_truncated,
                "source_path_count": len(source_paths),
                "source_path_preview": source_path_preview,
                "source_paths_truncated": source_paths_truncated,
                "row_count": target.get("row_count"),
                "summary": target.get("summary"),
            }
        )
    if inspected_candidates:
        return inspected_candidates

    preferred_suggestions = [suggestion for suggestion in state.suggestions if suggestion.get("name") in preferred_target_names]
    if preferred_suggestions:
        return preferred_suggestions

    return [
        {
            "name": target_name,
            "score": 0,
            "reasons": ["current run target"],
        }
        for target_name in state.preferred_targets
        if target_name
    ]


def build_planner_messages(state: SQLAgentState) -> list[HumanMessage]:
    """Build planner messages for the structured planning model."""
    payload = {
        "question": state.question,
        "source_files": state.source_files,
        "worker_context": state.worker_context,
        "skill_refs": _compact_skill_refs(state.skill_refs),
        "validation_feedback": state.validation_feedback,
        "candidate_targets": _planner_candidate_targets(state),
        "inspected_targets": [_compact_inspected_target(target) for target in state.inspected_targets],
        "previous_sql": state.candidate_sql,
        "previous_error": state.last_error,
        "repair_hints": state.repair_hints,
        "repair_count": state.repair_count,
    }
    return [
        HumanMessage(
            content=json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
            ),
        ),
    ]
