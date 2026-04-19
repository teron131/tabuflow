"""Workflow nodes for the deterministic tabular analysis agent."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from langchain.messages import HumanMessage, SystemMessage

from llm_harness.tools.sql.query import save_view
from llm_harness.tools.sql.sql_agent import answer_sql_question

from .prompts import FINAL_ANSWER_SYSTEM_PROMPT, build_task_prompt
from .state import TabularTaskState, append_trace

DEFAULT_VIEW_NAME = "analysis_result"
VIEW_NAME_STOP_WORDS = {"a", "an", "and", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
StateUpdate = dict[str, Any]


def suggest_view_name(task: str) -> str:
    """Build a deterministic snake_case view name from the task."""
    tokens = [token for token in re.findall(r"[a-z0-9]+", task.lower()) if token not in VIEW_NAME_STOP_WORDS]
    base = "_".join(tokens[:6]) or DEFAULT_VIEW_NAME
    if base[0].isdigit():
        base = f"analysis_{base}"
    return base


def collect_targets(extraction_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return compact extracted target metadata for the final answer payload."""
    targets: list[dict[str, Any]] = []
    for extraction in extraction_results:
        for table in extraction.get("tables", []):
            targets.append(
                {
                    "source_path": extraction.get("path"),
                    "table_name": table.get("table_name"),
                    "typed_view_name": table.get("typed_view_name"),
                    "row_count": table.get("row_count"),
                }
            )
    return targets


def traced_update(
    state: TabularTaskState,
    trace_message: str,
    **values: Any,
) -> StateUpdate:
    """Return one state update with a trace entry appended."""
    return {
        **values,
        "trace": append_trace(state, trace_message),
    }


def error_update(
    state: TabularTaskState,
    *,
    last_error: str,
    trace_message: str,
    **values: Any,
) -> StateUpdate:
    """Return one error state update with the shared trace shape."""
    return traced_update(
        state,
        trace_message,
        status="error",
        last_error=last_error,
        **values,
    )


def make_extract_node(tabular_tools: list[Any]):
    """Create the extraction node from the tabular tool surface."""
    extract_tabular = next((tool for tool in tabular_tools if getattr(tool, "name", None) == "extract_tabular"), None)
    if extract_tabular is None:
        raise ValueError("Missing tool: extract_tabular")

    def extract_node(state: TabularTaskState) -> StateUpdate:
        extraction_results: list[dict[str, Any]] = []
        database_paths: set[str] = set()
        for source_file in state.source_files:
            extraction = extract_tabular.invoke({"path": source_file})
            extraction_results.append(extraction)
            if extraction.get("status") != "loaded":
                return error_update(
                    state,
                    extraction_results=extraction_results,
                    last_error=extraction.get("message", f"Extraction failed for {source_file}"),
                    trace_message=f"extract failed for {source_file}",
                )
            if database_path := extraction.get("database_path"):
                database_paths.add(str(database_path))

        if len(database_paths) != 1:
            return error_update(
                state,
                extraction_results=extraction_results,
                last_error="Expected one shared SQLite database path after extraction.",
                trace_message="extract produced inconsistent database paths",
            )

        database_path = next(iter(database_paths))
        targets = collect_targets(extraction_results)
        return traced_update(
            state,
            f"extracted {len(targets)} targets into {database_path}",
            status="extracted",
            database_path=database_path,
            extraction_results=extraction_results,
            extracted_targets=targets,
        )

    return extract_node


def make_sql_node(*, llm: Any):
    """Create the SQL-agent node."""

    def sql_node(state: TabularTaskState) -> StateUpdate:
        if not state.database_path:
            return error_update(
                state,
                last_error="No SQLite database path was available for SQL analysis.",
                trace_message="sql skipped because database_path was missing",
            )

        view_name = suggest_view_name(state.task)
        with sqlite3.connect(state.database_path) as connection:
            connection.execute(f'DROP VIEW IF EXISTS "{view_name}"')
            connection.commit()

        sql_output = answer_sql_question(
            state.task,
            llm=llm,
            database_path=state.database_path,
        )
        trace_message = f"sql agent finished with status={sql_output.status}"
        if sql_output.selected_targets:
            targets = ", ".join(sql_output.selected_targets)
            trace_message += f" on {targets}"
        else:
            trace_message += f" after clearing stale view {view_name}"
        return traced_update(
            state,
            trace_message,
            status=sql_output.status,
            sql_agent_output=sql_output.model_dump(mode="json"),
            selected_targets=sql_output.selected_targets,
            candidate_sql=sql_output.candidate_sql,
            sql_result=sql_output.result,
            last_error=None if sql_output.status == "complete" else sql_output.last_error,
        )

    return sql_node


def save_node(state: TabularTaskState) -> StateUpdate:
    """Save the final SQL query result as a reusable SQLite view."""
    if not state.candidate_sql or not state.database_path:
        return error_update(
            state,
            last_error="No executable SQL was available to save as a view.",
            trace_message="save skipped because candidate_sql was missing",
        )

    view_name = suggest_view_name(state.task)
    saved_view = save_view(
        state.candidate_sql,
        view_name,
        database_path=state.database_path,
        replace=True,
    )
    if saved_view.get("status") != "ok":
        return error_update(
            state,
            saved_view_name=view_name,
            saved_view=saved_view,
            last_error=saved_view.get("message", f"Failed to save view {view_name}"),
            trace_message=f"save failed for view {view_name}",
        )

    return traced_update(
        state,
        f"saved result as view {view_name}",
        status="saved",
        saved_view_name=view_name,
        saved_view=saved_view,
        last_error=None,
    )


def make_answer_node(llm: Any, *, prompt: str):
    """Create the final answer composition node."""

    def answer_node(state: TabularTaskState) -> StateUpdate:
        response = llm.invoke(
            [
                SystemMessage(content=FINAL_ANSWER_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        f"{build_task_prompt(prompt, state.task, state.source_files)}\n\n"
                        "Execution result:\n"
                        f"{
                            json.dumps(
                                {
                                    'task': state.task,
                                    'status': state.status,
                                    'source_files': state.source_files,
                                    'database_path': state.database_path,
                                    'extracted_targets': state.extracted_targets,
                                    'selected_targets': state.selected_targets,
                                    'candidate_sql': state.candidate_sql,
                                    'sql_result': state.sql_result,
                                    'saved_view_name': state.saved_view_name,
                                    'last_error': state.last_error,
                                },
                                ensure_ascii=True,
                                sort_keys=True,
                            )
                        }"
                    )
                ),
            ]
        )
        return traced_update(
            state,
            "generated final answer",
            final_answer=getattr(response, "content", str(response)),
        )

    return answer_node
