"""LangGraph nodes and routes for the SQL stage."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel

from ...file_management import edit_sql_file, read_sql_file, read_sql_hashlines, write_sql_file
from ...tools.sql.query import run_query, suggest_sql_error_repair
from ..base import ApplicationAgent
from ..trace_utils import SQL_STAGE, append_stage_trace
from .prompts import (
    SQL_DRAFT_SYSTEM_PROMPT,
    SQL_RUNTIME_REPAIR_SYSTEM_PROMPT,
    build_draft_messages,
    build_runtime_repair_messages,
)
from .state import DraftFn, RuntimeRepairFn, QueryStageState, SQLDraft, SQLRuntimeRepair


QueryStageUpdate = dict[str, Any]


def build_sql_drafter(llm: BaseChatModel) -> DraftFn:
    """Build the SQL draft function from the orchestrator's shared model."""
    draft_agent = create_agent(
        model=llm,
        tools=[],
        system_prompt=SQL_DRAFT_SYSTEM_PROMPT,
        response_format=ToolStrategy(SQLDraft),
        name="sql_drafter",
    )

    def drafter(state: QueryStageState) -> SQLDraft:
        """Draft the next SQL file contents."""
        result = draft_agent.invoke({"messages": build_draft_messages(state)})
        return ApplicationAgent.get_structured_response(
            result,
            SQLDraft,
            agent_name="sql_drafter",
        )

    return drafter


def build_sql_runtime_repairer(llm: BaseChatModel) -> RuntimeRepairFn:
    """Build the SQL runtime repair function from the orchestrator's shared model."""
    repair_agent = create_agent(
        model=llm,
        tools=[],
        system_prompt=SQL_RUNTIME_REPAIR_SYSTEM_PROMPT,
        response_format=ToolStrategy(SQLRuntimeRepair),
        name="sql_runtime_repairer",
    )

    def runtime_repairer(state: QueryStageState) -> SQLRuntimeRepair:
        """Repair the current SQL file after a runtime execution error."""
        if not state.sql_path:
            return SQLRuntimeRepair()

        sql_hashlines = state.sql_hashlines
        if sql_hashlines is None:
            hashline_result = read_sql_hashlines(
                state.sql_path,
                run_id=state.run_id,
            )
            if hashline_result["status"] != "ok":
                return SQLRuntimeRepair()
            sql_hashlines = str(hashline_result["hashlines"])

        result = repair_agent.invoke(
            {
                "messages": build_runtime_repair_messages(
                    state,
                    sql_hashlines=sql_hashlines,
                )
            }
        )
        return ApplicationAgent.get_structured_response(
            result,
            SQLRuntimeRepair,
            agent_name="sql_runtime_repairer",
        )

    return runtime_repairer


def _append_trace(state: QueryStageState, message: str) -> list[str]:
    """Append one trace message."""
    return append_stage_trace(state.trace, SQL_STAGE, message)


def _error_update(
    state: QueryStageState,
    *,
    last_error: str,
    trace_message: str,
    **extra: Any,
) -> QueryStageUpdate:
    """Return the standard SQL-stage error update."""
    return {
        "status": "error",
        "last_error": last_error,
        "trace": _append_trace(state, trace_message),
        **extra,
    }


def _preferred_target_names(state: QueryStageState) -> list[str]:
    """Return orchestrator-provided target names in first-seen order."""
    target_names: list[str] = []
    seen_names: set[str] = set()
    for name in state.preferred_targets:
        target_name = name.strip()
        if not target_name or target_name in seen_names:
            continue
        seen_names.add(target_name)
        target_names.append(target_name)
    for target in state.extracted_targets:
        target_name = str(target.get("typed_view_name") or target.get("table_name") or "").strip()
        if not target_name or target_name in seen_names:
            continue
        seen_names.add(target_name)
        target_names.append(target_name)
    return target_names


def _runtime_repair_hints(state: QueryStageState, error_message: str) -> list[dict[str, Any]]:
    """Return deterministic hints for SQLite/runtime repair."""
    return suggest_sql_error_repair(
        error_message,
        available_targets=sorted(set(_preferred_target_names(state))),
        target_columns={},
    )


def _file_error_update(
    state: QueryStageState,
    result: dict[str, Any],
    *,
    default_message: str,
    trace_prefix: str,
    **extra: Any,
) -> QueryStageUpdate:
    """Return a SQL-stage error update from a file-management result."""
    message = str(result.get("message") or default_message)
    return _error_update(
        state,
        last_error=message,
        trace_message=f"{trace_prefix}: {message}",
        result=result,
        **extra,
    )


def make_write_node(
    drafter: DraftFn,
) -> Callable[
    [QueryStageState],
    QueryStageUpdate,
]:
    """Create the node that produces and writes the SQL artifact file."""

    def write_node(state: QueryStageState) -> QueryStageUpdate:
        """Draft SQL from shared orchestrator context and persist it."""
        target_names = _preferred_target_names(state)
        if not target_names:
            return _error_update(
                state,
                last_error="SQL stage requires orchestrator-provided targets.",
                trace_message="blocked because no orchestrator targets were provided",
            )

        draft = drafter(state)
        if not draft.sql.strip():
            return _error_update(
                state,
                last_error="SQL draft did not produce query text.",
                trace_message="write skipped because SQL draft was empty",
            )

        selected_targets = draft.selected_targets or target_names
        write_result = write_sql_file(
            draft.sql,
            state.sql_path,
            run_id=state.run_id,
            description=state.message,
            filename_hint=draft.filename_hint,
            selected_targets=selected_targets,
        )
        if write_result["status"] != "ok":
            return _file_error_update(
                state,
                write_result,
                default_message="Failed to write SQL artifact.",
                trace_prefix="write failed",
                selected_targets=selected_targets,
                sql_path=write_result.get("sql_path") or state.sql_path,
            )

        return {
            "status": "written",
            "sql_path": write_result["sql_path"],
            "candidate_sql": write_result["sql"].strip(),
            "selected_targets": selected_targets,
            "trace": _append_trace(state, f"wrote SQL file {write_result['sql_path']}"),
        }

    return write_node


def execute_node(state: QueryStageState) -> QueryStageUpdate:
    """Execute SQL by reading the current SQL artifact file."""
    if state.status == "error":
        return {
            "trace": _append_trace(state, "execute skipped because SQL write failed"),
        }

    if not state.sql_path:
        return _error_update(
            state,
            last_error="No SQL artifact path was available for execution.",
            trace_message="execute skipped because no SQL file was available",
        )

    read_result = read_sql_file(
        state.sql_path,
        run_id=state.run_id,
    )
    if read_result["status"] != "ok":
        return _file_error_update(
            state,
            read_result,
            default_message="Failed to read SQL artifact.",
            trace_prefix="execute failed to read SQL file",
            sql_path=read_result.get("sql_path") or state.sql_path,
        )

    candidate_sql = str(read_result["sql"]).strip()
    if not candidate_sql:
        return _error_update(
            state,
            last_error="No SQL query was available for execution.",
            trace_message="execute skipped because SQL file was empty",
            sql_path=read_result["sql_path"],
        )

    attempts = state.attempts + 1
    result = run_query(
        candidate_sql,
        database_path=state.database_path,
    )
    if result["status"] == "ok":
        return {
            "status": "complete",
            "sql_path": read_result["sql_path"],
            "candidate_sql": candidate_sql,
            "attempts": attempts,
            "result": result,
            "last_error": None,
            "trace": _append_trace(state, f"execute succeeded on attempt {attempts}"),
        }

    error_message = str(result["message"])
    repair_hints = _runtime_repair_hints(state, error_message)
    trace_message = f"execute failed on attempt {attempts}: {error_message}"
    if repair_hints:
        trace_message += f" ({len(repair_hints)} repair hints)"
    return {
        "status": "needs_repair",
        "sql_path": read_result["sql_path"],
        "candidate_sql": candidate_sql,
        "attempts": attempts,
        "repair_hints": repair_hints,
        "result": result,
        "last_error": error_message,
        "trace": _append_trace(state, trace_message),
    }


def make_repair_sql_node(
    repairer: RuntimeRepairFn,
) -> Callable[
    [QueryStageState],
    QueryStageUpdate,
]:
    """Create the repair_sql node using the supplied repairer."""

    def repair_sql_node(state: QueryStageState) -> QueryStageUpdate:
        """Apply hashline edits for SQLite/runtime execution errors."""
        if not state.sql_path:
            return _error_update(
                state,
                last_error="No SQL artifact path was available for repair.",
                trace_message="runtime repair skipped because no SQL file was available",
            )

        hashline_result = read_sql_hashlines(
            state.sql_path,
            run_id=state.run_id,
        )
        if hashline_result["status"] != "ok":
            return _file_error_update(
                state,
                hashline_result,
                default_message="Failed to read SQL hashlines.",
                trace_prefix="runtime repair failed to read SQL file",
            )

        repair_state = state.model_copy(
            update={
                "repair_count": state.repair_count + 1,
                "sql_hashlines": str(hashline_result["hashlines"]),
            }
        )
        repair = repairer(repair_state)
        if not repair.edits:
            return _error_update(
                state,
                last_error="Runtime repair did not produce any SQL file edits.",
                trace_message="runtime repair produced no edits",
                repair_count=repair_state.repair_count,
            )

        edit_result = edit_sql_file(
            state.sql_path,
            repair.edits,
            run_id=state.run_id,
        )
        if edit_result["status"] != "ok":
            return _file_error_update(
                state,
                edit_result,
                default_message="Failed to edit SQL file.",
                trace_prefix="runtime repair failed to edit SQL file",
                repair_count=repair_state.repair_count,
            )

        return {
            "status": "repaired",
            "repair_count": repair_state.repair_count,
            "candidate_sql": edit_result["sql"].strip(),
            "sql_path": edit_result["sql_path"],
            "trace": _append_trace(state, f"runtime repair pass {repair_state.repair_count} edited SQL file"),
        }

    return repair_sql_node
