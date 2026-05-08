"""LangChain tools that expose orchestrator stage graphs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.runnables.config import patch_config
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from langgraph.prebuilt import InjectedState
from langgraph.types import Command

from ..prep_stage import PrepStage
from ..query_stage import DraftFn, RuntimeRepairFn
from ..validation_stage import ValidationStage
from .nodes import OrchestratorNodes
from .state import OrchestratorState

PREP_RECURSION_LIMIT_PER_SOURCE_FILE = 30
MIN_PREP_RECURSION_LIMIT = PREP_RECURSION_LIMIT_PER_SOURCE_FILE * 3
TOOL_STATE_EXCLUDE = {"messages", "structured_response"}
MAX_VISIBLE_TRACE_ITEMS = 80


def _merge_state_update(state: OrchestratorState, update: dict[str, Any]) -> OrchestratorState:
    """Apply one graph-node update outside LangGraph's reducer machinery."""
    payload = state.model_dump(mode="python")
    update_payload = dict(update)
    update_messages = update_payload.pop("messages", None)
    if update_messages:
        payload["messages"] = [*payload.get("messages", []), *update_messages]
    payload.update(update_payload)
    return OrchestratorState.model_validate(payload)


def _compact_state(state: OrchestratorState | dict[str, Any]) -> dict[str, Any]:
    """Return a JSON-safe state payload suitable for tool results."""
    return OrchestratorState.model_validate(state).model_dump(mode="json", exclude=TOOL_STATE_EXCLUDE)


def _trace_payload(state_payload: dict[str, Any]) -> list[str]:
    """Return compact trace strings for UI flattening."""
    trace = state_payload.get("trace") or []
    if not isinstance(trace, list):
        return []
    return [str(item) for item in trace[-MAX_VISIBLE_TRACE_ITEMS:] if str(item).strip()]


def _tool_command(
    *,
    tool_name: str,
    tool_call_id: str,
    state_payload: dict[str, Any],
    visible_payload: dict[str, Any],
) -> Command:
    """Return a tool result that updates graph state without exposing full state."""
    return Command(
        update={
            **state_payload,
            "messages": [
                ToolMessage(
                    content=json.dumps(visible_payload, ensure_ascii=True),
                    name=tool_name,
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


def _prep_visible_payload(state_payload: dict[str, Any]) -> dict[str, Any]:
    """Return the compact prep result shown to the model."""
    extracted_sql_artifacts = state_payload.get("extracted_sql_artifacts") or []
    sql_artifact_names = [
        str(sql_artifact.get("typed_view_name") or sql_artifact.get("table_name"))
        for sql_artifact in extracted_sql_artifacts
        if sql_artifact.get("typed_view_name") or sql_artifact.get("table_name")
    ]
    prep_output = state_payload.get("prep_output") or {}
    return {
        "status": prep_output.get("status") or ("prepared" if sql_artifact_names else "error"),
        "database_path": state_payload.get("database_path"),
        "prepared_state_available": bool(sql_artifact_names),
        "sql_artifact_count": len(sql_artifact_names),
        "preferred_sql_artifacts": state_payload.get("preferred_sql_artifacts") or sql_artifact_names,
        "trace": _trace_payload(state_payload),
    }


def _query_visible_payload(state_payload: dict[str, Any]) -> dict[str, Any]:
    """Return the compact query result shown to the model."""
    artifact = state_payload.get("artifact") or {}
    return {
        "status": artifact.get("status") or state_payload.get("status"),
        "outcome": artifact.get("outcome"),
        "completion_reason": artifact.get("completion_reason"),
        "content": state_payload.get("content"),
        "saved_view_name": artifact.get("saved_view_name"),
        "sql_path": artifact.get("sql_path") or state_payload.get("sql_path"),
        "sql_result": artifact.get("sql_result"),
        "last_error": artifact.get("last_error") or state_payload.get("last_error"),
        "trace": _trace_payload(state_payload),
    }


def _reset_query_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Clear per-query fields while preserving prepared data context."""
    return {
        **payload,
        "content": "",
        "artifact": {},
        "validation_feedback": None,
        "validation_attempts": 0,
        "status": "pending",
        "sql_path": None,
        "reuse_existing_sql": False,
        "related_sql_artifacts": [],
        "selected_sql_artifacts": [],
        "candidate_sql": None,
        "repair_hints": [],
        "result": None,
        "attempts": 0,
        "last_error": None,
        "sql_hashlines": None,
        "repair_count": 0,
    }


def make_orchestrator_stages(
    *,
    prompt: str = "",
    root_dir: str | Path | None = None,
    llm: BaseChatModel | None = None,
    prep_stage: PrepStage | None = None,
    sql_drafter: DraftFn | None = None,
    sql_runtime_repairer: RuntimeRepairFn | None = None,
    validation_stage: ValidationStage | None = None,
) -> list[BaseTool]:
    """Build callable orchestrator stages for the user-facing agent."""
    nodes = OrchestratorNodes(
        prompt=prompt,
        root_dir=root_dir,
        llm=llm,
        prep_stage=prep_stage,
        sql_drafter=sql_drafter,
        sql_runtime_repairer=sql_runtime_repairer,
        validation_stage=validation_stage,
    )
    prep_graph = nodes.prep_stage_graph()
    query_graph = nodes.query_stage_graph()

    @tool("prep_stage")
    def prep_stage(
        message: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        orchestrator_state: Annotated[Any, InjectedState],
        source_files: list[str] | None = None,
        max_validation_retries: int = 2,
    ) -> Command:
        """Prepare source files and store compact state for later query_stage calls."""
        current_state = OrchestratorState.model_validate(orchestrator_state)
        safe_source_files = source_files or current_state.source_files
        state = OrchestratorState(
            messages=[HumanMessage(content=message, name="user")],
            source_files=safe_source_files,
            max_validation_retries=max_validation_retries,
        )
        state = _merge_state_update(
            state,
            nodes.skill_context(state),
        )
        result = prep_graph.invoke(
            state.model_dump(mode="python"),
            config=patch_config(
                None,
                recursion_limit=max(
                    MIN_PREP_RECURSION_LIMIT,
                    PREP_RECURSION_LIMIT_PER_SOURCE_FILE * len(safe_source_files),
                ),
            ),
        )
        state_payload = _compact_state(result)
        return _tool_command(
            tool_name="prep_stage",
            tool_call_id=tool_call_id,
            state_payload=state_payload,
            visible_payload=_prep_visible_payload(state_payload),
        )

    @tool("query_stage")
    def query_stage(
        message: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        orchestrator_state: Annotated[Any, InjectedState],
        max_validation_retries: int | None = None,
    ) -> Command:
        """Query prepared data, validate the result, save a view, and store result state."""
        payload = _reset_query_fields(_compact_state(orchestrator_state))
        payload["messages"] = [HumanMessage(content=message, name="user")]
        payload.setdefault("source_files", [])
        if max_validation_retries is not None:
            payload["max_validation_retries"] = max_validation_retries
        result = query_graph.invoke(
            OrchestratorState.model_validate(payload).model_dump(mode="python"),
        )
        state_payload = _compact_state(result)
        return _tool_command(
            tool_name="query_stage",
            tool_call_id=tool_call_id,
            state_payload=state_payload,
            visible_payload=_query_visible_payload(state_payload),
        )

    return [prep_stage, query_stage]
