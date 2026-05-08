"""LangGraph nodes and routes for the SQL stage."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel

from ...file_management import (
    edit_sql_file,
    read_sql_file,
    read_sql_hashlines,
    search_sql_artifacts,
    write_sql_file,
)
from ...pipelines.namer import ArtifactNamerFn
from ...tools.sql.query import run_query, suggest_sql_error_repair
from ..base import ApplicationAgent
from ..orchestrator.state import latest_user_message
from ..trace_utils import SQL_STAGE, append_stage_trace
from .prompts import (
    SQL_REUSE_SYSTEM_PROMPT,
    SQL_REPAIR_SYSTEM_PROMPT,
    SQL_WRITE_SYSTEM_PROMPT,
    build_existing_sql_messages,
    build_repair_messages,
    build_write_messages,
)
from .state import (
    ExistingSQLDecision,
    ExistingSQLSelectorFn,
    QueryStageState,
    SQLRepair,
    SQLRepairerFn,
    SQLWrite,
    SQLWriterFn,
)

QueryStageUpdate = dict[str, Any]
MAX_RELATED_SQL_ARTIFACTS = 5


def build_sql_writer(llm: BaseChatModel) -> SQLWriterFn:
    """Build the SQL write function from the orchestrator's shared model."""
    write_agent = create_agent(
        model=llm,
        tools=[],
        system_prompt=SQL_WRITE_SYSTEM_PROMPT,
        response_format=ToolStrategy(SQLWrite),
        name="sql_writer",
    )

    def writer(state: QueryStageState) -> SQLWrite:
        """Write the next SQL file contents."""
        result = write_agent.invoke({"messages": build_write_messages(state)})
        return ApplicationAgent.get_structured_response(
            result,
            SQLWrite,
            agent_name="sql_writer",
        )

    return writer


def build_sql_repairer(llm: BaseChatModel) -> SQLRepairerFn:
    """Build the SQL repair function from the orchestrator's shared model."""
    repair_agent = create_agent(
        model=llm,
        tools=[],
        system_prompt=SQL_REPAIR_SYSTEM_PROMPT,
        response_format=ToolStrategy(SQLRepair),
        name="sql_repairer",
    )

    def repairer(state: QueryStageState) -> SQLRepair:
        """Repair the current SQL file after a runtime execution error."""
        if not state.sql_path:
            return SQLRepair()

        sql_hashlines = state.sql_hashlines
        if sql_hashlines is None:
            hashline_result = read_sql_hashlines(
                state.sql_path,
                run_id=state.run_id,
            )
            if hashline_result["status"] != "ok":
                return SQLRepair()
            sql_hashlines = str(hashline_result["hashlines"])

        result = repair_agent.invoke(
            {
                "messages": build_repair_messages(
                    state,
                    sql_hashlines=sql_hashlines,
                )
            }
        )
        return ApplicationAgent.get_structured_response(
            result,
            SQLRepair,
            agent_name="sql_repairer",
        )

    return repairer


def build_existing_sql_selector(llm: BaseChatModel) -> ExistingSQLSelectorFn:
    """Build the selector that can reuse a ready existing SQL artifact."""
    selector_agent = create_agent(
        model=llm,
        tools=[],
        system_prompt=SQL_REUSE_SYSTEM_PROMPT,
        response_format=ToolStrategy(ExistingSQLDecision),
        name="existing_sql_selector",
    )

    def selector(state: QueryStageState) -> ExistingSQLDecision:
        """Choose whether one discovered SQL artifact is an exact reuse fit."""
        if not state.related_sql_artifacts:
            return ExistingSQLDecision(reason="No related SQL artifacts were discovered.")

        result = selector_agent.invoke({"messages": build_existing_sql_messages(state)})
        return ApplicationAgent.get_structured_response(
            result,
            ExistingSQLDecision,
            agent_name="existing_sql_selector",
        )

    return selector


def never_reuse_existing_sql(_: QueryStageState) -> ExistingSQLDecision:
    """Return the default decision when no selector model is available."""
    return ExistingSQLDecision(reason="No existing SQL selector is configured.")


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


def _preferred_sql_artifact_names(state: QueryStageState) -> list[str]:
    """Return orchestrator-provided sql_artifact names in first-seen order."""
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


def _matching_related_sql_artifact(
    related_sql_artifacts: list[dict[str, Any]],
    sql_path: str | None,
) -> dict[str, Any] | None:
    """Return the discovered artifact that matches a selector-chosen path."""
    if not sql_path:
        return None
    normalized_sql_path = str(sql_path)
    for artifact in related_sql_artifacts:
        if normalized_sql_path in {str(artifact.get("sql_path") or ""), str(artifact.get("path") or "")}:
            return artifact
    return None


def _selected_sql_artifact_update(
    related_sql_artifacts: list[dict[str, Any]],
    selected_artifact: dict[str, Any],
) -> QueryStageUpdate:
    """Return state fields shared by direct reuse and write-context reuse."""
    return {
        "related_sql_artifacts": related_sql_artifacts,
        "sql_path": selected_artifact["sql_path"],
        "candidate_sql": selected_artifact.get("sql_preview"),
        "selected_sql_artifacts": list(selected_artifact.get("selected_sql_artifacts") or []),
    }


@dataclass
class QueryStageNodes:
    """Dependency-bound nodes for the SQL query stage."""

    selector: ExistingSQLSelectorFn
    writer: SQLWriterFn
    repairer: SQLRepairerFn
    artifact_namer: ArtifactNamerFn | None = None
    root_dir: str | Path | None = None

    def check_existing_sql(self, state: QueryStageState) -> QueryStageUpdate:
        """Look at related SQL artifacts before the query stage writes a new SQL file."""
        search_result = search_sql_artifacts(
            latest_user_message(state.messages),
            root_dir=self.root_dir,
            top_k=MAX_RELATED_SQL_ARTIFACTS,
        )
        if search_result.get("status") != "ok":
            return {
                "reuse_existing_sql": False,
                "related_sql_artifacts": [],
                "trace": _append_trace(state, f"check_existing_sql: could not inspect existing SQL artifacts: {search_result.get('message') or 'unknown error'}"),
            }

        related_sql_artifacts = list(search_result.get("artifacts") or [])
        if not related_sql_artifacts:
            return {
                "reuse_existing_sql": False,
                "related_sql_artifacts": [],
                "trace": _append_trace(state, "check_existing_sql: no related existing SQL artifacts found"),
            }

        decision_state = state.model_copy(
            update={
                "related_sql_artifacts": related_sql_artifacts,
            }
        )
        decision = self.selector(decision_state)
        selected_artifact = _matching_related_sql_artifact(
            related_sql_artifacts,
            decision.sql_path,
        )
        if decision.reuse_existing_sql and selected_artifact is not None:
            return {
                **_selected_sql_artifact_update(
                    related_sql_artifacts,
                    selected_artifact,
                ),
                "reuse_existing_sql": True,
                "trace": _append_trace(state, f"check_existing_sql: reusing existing SQL artifact {selected_artifact['sql_path']}"),
            }

        if decision.use_as_write_context and selected_artifact is not None:
            reason = decision.reason.strip() or "related SQL artifact is useful write context"
            return {
                **_selected_sql_artifact_update(
                    related_sql_artifacts,
                    selected_artifact,
                ),
                "reuse_existing_sql": False,
                "trace": _append_trace(state, f"check_existing_sql: adapting existing SQL artifact {selected_artifact['sql_path']} because {reason}"),
            }

        reason = decision.reason.strip() or "existing SQL artifact was not an exact match"
        return {
            "reuse_existing_sql": False,
            "related_sql_artifacts": related_sql_artifacts,
            "trace": _append_trace(state, f"check_existing_sql: writing from zero because {reason}"),
        }

    def write_sql(self, state: QueryStageState) -> QueryStageUpdate:
        """Write SQL from shared orchestrator context and persist it."""
        sql_artifact_names = _preferred_sql_artifact_names(state)
        if not sql_artifact_names:
            return _error_update(
                state,
                last_error="SQL stage requires orchestrator-provided SQL artifacts.",
                trace_message="write_sql: blocked because no orchestrator SQL artifacts were provided",
            )

        write = self.writer(state)
        if not write.sql.strip():
            return _error_update(
                state,
                last_error="SQL write did not produce query text.",
                trace_message="write_sql: skipped because SQL write was empty",
            )

        selected_sql_artifacts = write.selected_sql_artifacts or sql_artifact_names
        filename_hint = None
        if self.artifact_namer is not None:
            filename_hint = self.artifact_namer(
                "\n".join(
                    [
                        f"User request: {latest_user_message(state.messages)}",
                        f"Selected SQL artifacts: {', '.join(selected_sql_artifacts)}",
                        f"SQL:\n{write.sql}",
                    ]
                )
            )
        write_result = write_sql_file(
            write.sql,
            state.sql_path,
            run_id=state.run_id,
            description=latest_user_message(state.messages),
            filename_hint=filename_hint,
            selected_sql_artifacts=selected_sql_artifacts,
        )
        if write_result["status"] != "ok":
            return _file_error_update(
                state,
                write_result,
                default_message="Failed to write SQL artifact.",
                trace_prefix="write_sql: failed",
                selected_sql_artifacts=selected_sql_artifacts,
                sql_path=write_result.get("sql_path") or state.sql_path,
            )

        return {
            "status": "written",
            "sql_path": write_result["sql_path"],
            "candidate_sql": write_result["sql"].strip(),
            "selected_sql_artifacts": selected_sql_artifacts,
            "trace": _append_trace(state, f"write_sql: wrote SQL file {write_result['sql_path']}"),
        }

    def repair_sql(self, state: QueryStageState) -> QueryStageUpdate:
        """Apply hashline edits for SQLite/runtime execution errors."""
        if not state.sql_path:
            return _error_update(
                state,
                last_error="No SQL artifact path was available for repair.",
                trace_message="repair_sql: skipped because no SQL file was available",
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
                trace_prefix="repair_sql: failed to read SQL file",
            )

        repair_state = state.model_copy(
            update={
                "repair_count": state.repair_count + 1,
                "sql_hashlines": str(hashline_result["hashlines"]),
            }
        )
        repair = self.repairer(repair_state)
        if not repair.edits:
            return _error_update(
                state,
                last_error="Runtime repair did not produce any SQL file edits.",
                trace_message="repair_sql: produced no edits",
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
                trace_prefix="repair_sql: failed to edit SQL file",
                repair_count=repair_state.repair_count,
            )

        return {
            "status": "repaired",
            "repair_count": repair_state.repair_count,
            "candidate_sql": edit_result["sql"].strip(),
            "sql_path": edit_result["sql_path"],
            "trace": _append_trace(state, f"repair_sql: pass {repair_state.repair_count} edited SQL file"),
        }


def route_after_existing_sql_check(state: QueryStageState) -> str:
    """Route the query stage after inspecting existing SQL artifacts."""
    if state.reuse_existing_sql and state.sql_path:
        return "execute_sql"
    return "write_sql"


def _runtime_repair_hints(state: QueryStageState, error_message: str) -> list[dict[str, Any]]:
    """Return deterministic hints for SQLite/runtime repair."""
    sql_artifact_columns: dict[str, list[str]] = {}
    for sql_artifact in state.extracted_sql_artifacts:
        sql_artifact_name = str(sql_artifact.get("typed_view_name") or sql_artifact.get("table_name") or "").strip()
        if not sql_artifact_name:
            continue
        columns = sql_artifact.get("typed_columns") or sql_artifact.get("db_columns") or sql_artifact.get("columns") or []
        sql_artifact_columns[sql_artifact_name] = [str(column) for column in columns if str(column).strip()]
    return suggest_sql_error_repair(
        error_message,
        available_sql_artifacts=sorted(set(_preferred_sql_artifact_names(state))),
        sql_artifact_columns=sql_artifact_columns,
    )


def execute_sql_node(state: QueryStageState) -> QueryStageUpdate:
    """Execute SQL by reading the current SQL artifact file."""
    if state.status == "error":
        return {
            "trace": _append_trace(state, "execute_sql: skipped because SQL write failed"),
        }

    if not state.sql_path:
        return _error_update(
            state,
            last_error="No SQL artifact path was available for execution.",
            trace_message="execute_sql: skipped because no SQL file was available",
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
            trace_prefix="execute_sql: failed to read SQL file",
            sql_path=read_result.get("sql_path") or state.sql_path,
        )

    candidate_sql = str(read_result["sql"]).strip()
    if not candidate_sql:
        return _error_update(
            state,
            last_error="No SQL query was available for execution.",
            trace_message="execute_sql: skipped because SQL file was empty",
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
            "trace": _append_trace(state, f"execute_sql: succeeded on attempt {attempts}"),
        }

    error_message = str(result["message"])
    repair_hints = _runtime_repair_hints(state, error_message)
    trace_message = f"execute_sql: failed on attempt {attempts}: {error_message}"
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
