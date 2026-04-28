"""LangChain tools that expose orchestrator stage graphs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import patch_config
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from ..prep_stage import PrepStage
from ..query_stage import DraftFn, RuntimeRepairFn
from ..validation_stage import ValidationStage
from .nodes import OrchestratorNodes
from .state import OrchestratorState

PREP_RECURSION_LIMIT_PER_SOURCE_FILE = 30
MIN_PREP_RECURSION_LIMIT = PREP_RECURSION_LIMIT_PER_SOURCE_FILE * 3
TOOL_STATE_EXCLUDE = {"messages", "structured_response"}


class PrepStageArgs(BaseModel):
    """Input for the prep-stage tool."""

    message: str = Field(description="User request that needs source-file preparation.")
    source_files: list[str] = Field(default_factory=list, description="Source files to inspect and prepare.")
    max_validation_retries: int = Field(default=2, description="Retry budget to carry into later query-stage calls.")


class QueryStageArgs(BaseModel):
    """Input for the query-stage tool."""

    message: str = Field(description="User request to answer using already prepared data.")
    prepared_state: dict[str, Any] = Field(description="Compact state returned by the prep_stage tool.")
    max_validation_retries: int | None = Field(default=None, description="Optional retry budget override for this query.")


def _prep_recursion_limit(source_files: list[str]) -> int:
    """Return the prep-stage recursion budget for one tool call."""
    return max(
        MIN_PREP_RECURSION_LIMIT,
        PREP_RECURSION_LIMIT_PER_SOURCE_FILE * len(source_files),
    )


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
        "selected_targets": [],
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

    def prep_stage(
        message: str,
        source_files: list[str] | None = None,
        max_validation_retries: int = 2,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """Prepare source files and return compact state for later query_stage calls."""
        safe_source_files = source_files or []
        state = OrchestratorState(
            messages=[HumanMessage(content=message)],
            source_files=safe_source_files,
            max_validation_retries=max_validation_retries,
        )
        state = _merge_state_update(
            state,
            nodes.skill_context(state, config=config),
        )
        result = prep_graph.invoke(
            state.model_dump(mode="python"),
            config=patch_config(
                config,
                recursion_limit=_prep_recursion_limit(safe_source_files),
            ),
        )
        return _compact_state(result)

    def query_stage(
        message: str,
        prepared_state: dict[str, Any],
        max_validation_retries: int | None = None,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """Query prepared data, validate the result, save a view, and return compact state."""
        payload = _reset_query_fields(dict(prepared_state))
        payload["messages"] = [HumanMessage(content=message)]
        payload.setdefault("source_files", [])
        if max_validation_retries is not None:
            payload["max_validation_retries"] = max_validation_retries
        result = query_graph.invoke(
            OrchestratorState.model_validate(payload).model_dump(mode="python"),
            config=config,
        )
        return _compact_state(result)

    return [
        StructuredTool.from_function(
            prep_stage,
            name="prep_stage",
            description="Prepare source files for later SQL/data analysis. Returns compact prepared state.",
            args_schema=PrepStageArgs,
        ),
        StructuredTool.from_function(
            query_stage,
            name="query_stage",
            description="Run SQL analysis on prepared state, validate it, save a view, and return compact result state.",
            args_schema=QueryStageArgs,
        ),
    ]
