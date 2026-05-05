"""Prompts and message payloads for the orchestrator-owned SQL stage."""

from __future__ import annotations

import json
from typing import Any

from langchain.messages import HumanMessage

from ..orchestrator.state import latest_user_message
from .state import QueryStageState

MAX_AGENT_SKILL_REF_PREVIEW = 8

SQL_DRAFT_SYSTEM_PROMPT = """Draft one read-only SQLite query for the SQL stage.

Rules:
- The orchestrator/prep stages already chose the message, database, and allowed targets. Trust that context.
- Use only SELECT, WITH, or EXPLAIN.
- Use only tables/views from `allowed_targets` and `target_context`.
- `target_context` is shared graph state from the orchestrator/prep stage; use its exact column names and quote identifiers that contain spaces, punctuation, or mixed case.
- Do not invent normalized columns such as `account`, `account_id`, or `cost_usd` unless the target schema already exposes them or you define them in an earlier CTE from exact source columns.
- Treat loaded skill references inside `worker_context` as the source of truth for domain SQL. If a skill SQL reference uses placeholders, preserve its raw-column mapping pattern and replace only placeholders with discovered target names/literals.
- If a skill asks for one saved result view such as `analysis_result`, draft the full SELECT/WITH body that will become that view. Do not select from the future saved view name unless that name is already present in `allowed_targets`.
- If the message or skill asks for multiple result grains, such as summary, category, and account/customer rows, the SQL must produce all requested row types from real source data. Do not satisfy missing grains with empty UNION arms, `WHERE 1 = 0`, duplicated total rows, or placeholder labels.
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
- Use loaded skill references inside `worker_context` when an execution error shows the SQL drifted from the reference contract.
- If a missing-column error comes from a fabricated normalized schema, rebuild the affected query from the skill reference and exact `target_context` columns instead of trying another alias.
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
                "text_reference_count": sum(1 for reference in reference_items if str(reference.get("kind", "")).lower() != "sql"),
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
            "columns": target.get("columns") or [],
            "db_columns": target.get("db_columns") or [],
            "typed_columns": target.get("typed_columns") or [],
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
        "message": latest_user_message(state.messages),
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
        "message": latest_user_message(state.messages),
        "worker_context": state.worker_context,
        "skill_refs": _compact_skill_refs(state.skill_refs),
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
