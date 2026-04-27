"""Prompts and message payloads for the orchestrator-owned SQL stage."""

from __future__ import annotations

import json
from typing import Any

from langchain.messages import HumanMessage

from .state import QueryStageState

MAX_AGENT_SKILL_REF_PREVIEW = 8

SQL_DRAFT_SYSTEM_PROMPT = """Draft one read-only SQLite query for the SQL stage.

Rules:
- The orchestrator/prep stages already chose the message, database, and allowed targets. Trust that context.
- Use only SELECT, WITH, or EXPLAIN.
- Use only tables/views from `allowed_targets` and `target_context`.
- `target_context` is shared graph state from the orchestrator/prep stage; do not ask to inspect or discover more targets.
- Treat `message` as the user request and `validation_feedback` as semantic retry guidance from the validation stage.
- If `validation_feedback` is present, revise the query to address it directly.
- Do not ask clarifying questions, discover targets, or judge final request fulfillment.
- Set `filename_hint` to 3-4 kebab-case noun words that describe the query artifact, for example `gcp-group-totals-sep`.
- Focus `filename_hint` on concrete nouns from the metric, entity, grouping, source, and period. Avoid verbs like show/get/list and filler words.
"""

SQL_RUNTIME_REPAIR_SYSTEM_PROMPT = """Repair a SQL file so SQLite can execute it.

Rules:
- Only fix SQLite execution errors, syntax errors, or identifier errors.
- Do not change the business meaning unless required to fix execution.
- Do not judge whether the result satisfies the message; validation owns that.
- Use hashline refs from `sql_hashlines` and return only hashline edits.
- Prefer deterministic `repair_hints` when they identify replacement columns or targets.
- Preserve the SQL comment header unless the broken line is inside the header.
"""


def _compact_skill_refs(skill_refs: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a bounded preview of worker-visible skill references."""
    preview_refs = skill_refs[:MAX_AGENT_SKILL_REF_PREVIEW]
    compact_refs: list[dict[str, Any]] = []
    for skill_ref in preview_refs:
        references = skill_ref.get("references", [])
        reference_items = references if isinstance(references, list) else []
        compact_refs.append(
            {
                "name": skill_ref.get("name"),
                "path": skill_ref.get("path"),
                "instructions_path": (skill_ref.get("instructions") or {}).get("relative_path"),
                "reference_count": len(reference_items),
                "sql_reference_count": sum(1 for reference in reference_items if str(reference.get("kind", "")).lower() == "sql"),
            }
        )
    return {
        "count": len(skill_refs),
        "truncated": len(skill_refs) > MAX_AGENT_SKILL_REF_PREVIEW,
        "items": compact_refs,
    }


def _target_context(state: QueryStageState) -> list[dict[str, Any]]:
    """Return the best target context already carried by the orchestrator state."""
    return [
        {
            "source_path": target.get("source_path"),
            "table_name": target.get("table_name"),
            "typed_view_name": target.get("typed_view_name"),
            "row_count": target.get("row_count"),
        }
        for target in state.extracted_targets
    ]


def _allowed_targets(state: QueryStageState) -> list[str]:
    """Return allowed SQL targets from shared orchestrator state."""
    if state.preferred_targets:
        return state.preferred_targets
    return [str(target.get("typed_view_name") or target.get("table_name")) for target in state.extracted_targets if target.get("typed_view_name") or target.get("table_name")]


def _message_from_payload(payload: dict[str, Any]) -> list[HumanMessage]:
    """Serialize one structured payload for a model prompt."""
    return [
        HumanMessage(
            content=json.dumps(payload, ensure_ascii=True, sort_keys=True),
        ),
    ]


def build_draft_messages(state: QueryStageState) -> list[HumanMessage]:
    """Build draft messages for the structured SQL stage model."""
    payload = {
        "message": state.message,
        "source_files": state.source_files,
        "worker_context": state.worker_context,
        "skill_refs": _compact_skill_refs(state.skill_refs),
        "validation_feedback": state.validation_feedback,
        "allowed_targets": _allowed_targets(state),
        "target_context": _target_context(state),
        "sql_path": state.sql_path,
        "previous_sql": state.candidate_sql,
    }
    return _message_from_payload(payload)


def build_runtime_repair_messages(
    state: QueryStageState,
    *,
    sql_hashlines: str,
) -> list[HumanMessage]:
    """Build runtime-repair messages for SQLite execution errors."""
    payload = {
        "message": state.message,
        "allowed_targets": _allowed_targets(state),
        "target_context": _target_context(state),
        "sql_path": state.sql_path,
        "sql_hashlines": sql_hashlines,
        "sqlite_error": state.result,
        "last_error": state.last_error,
        "repair_hints": state.repair_hints,
        "repair_count": state.repair_count,
    }
    return _message_from_payload(payload)
