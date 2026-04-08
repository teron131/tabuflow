"""Deterministic tabular-to-SQL workflow for multicloud billing analysis."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Any, Literal

from langchain.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field

from llm_harness.clients.openai import ChatOpenAI
from llm_harness.tools.sql.query import save_view
from llm_harness.tools.sql.sql_agent import answer_sql_question
from llm_harness.tools.tabular.tools import make_tabular_tools

DEFAULT_MODEL_ENV = "FAST_LLM"
DEFAULT_MODEL = "openai/gpt-5.4-nano"
DEFAULT_REASONING_EFFORT: Literal["minimal", "low", "medium", "high", "xhigh"] = "high"
DEFAULT_VIEW_NAME = "analysis_result"
VIEW_NAME_STOP_WORDS = {"a", "an", "and", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}

FINAL_ANSWER_SYSTEM_PROMPT = """Write the final answer for a completed tabular-to-SQL analysis run.

Use only the provided structured execution results.

Rules:
- answer the user's task directly,
- respect the task prompt provided by the user message,
- mention the source file(s), the SQL target(s), and the saved view name when available,
- do not invent facts or explanations that are not present in the payload,
- keep the answer concise and audit-friendly,
- if the run is blocked or failed, explain that plainly instead of pretending it succeeded.
"""


class TabularTaskInput(BaseModel):
    """Public input for the deterministic tabular analysis graph."""

    task: str
    source_files: list[str]


class TabularTaskOutput(BaseModel):
    """Public output for the deterministic tabular analysis graph."""

    status: str = "pending"
    database_path: str | None = None
    extracted_targets: list[dict[str, Any]] = Field(default_factory=list)
    selected_targets: list[str] = Field(default_factory=list)
    candidate_sql: str | None = None
    sql_result: dict[str, Any] | None = None
    saved_view_name: str | None = None
    saved_view: dict[str, Any] | None = None
    final_answer: str | None = None
    last_error: str | None = None
    trace: list[str] = Field(default_factory=list)


class TabularTaskState(TabularTaskInput, TabularTaskOutput):
    """Internal graph state."""

    extraction_results: list[dict[str, Any]] = Field(default_factory=list)
    sql_agent_output: dict[str, Any] | None = None


def _append_trace(state: TabularTaskState, message: str) -> list[str]:
    """Append one trace message."""
    return [*state.trace, message]


def _tool_by_name(tools: list[Any], tool_name: str) -> Any:
    """Return one LangChain tool by name."""
    for tool in tools:
        if getattr(tool, "name", None) == tool_name:
            return tool
    raise ValueError(f"Missing tool: {tool_name}")


def _suggest_view_name(task: str) -> str:
    """Build a deterministic snake_case view name from the task."""
    tokens = [token for token in re.findall(r"[a-z0-9]+", task.lower()) if token not in VIEW_NAME_STOP_WORDS]
    base = "_".join(tokens[:6]) or DEFAULT_VIEW_NAME
    if base[0].isdigit():
        base = f"analysis_{base}"
    return base


def _source_file_list(source_files: list[str]) -> str:
    """Render the source file list once for prompts and console output."""
    return "\n".join(f"- {source_file}" for source_file in source_files) or "- (none)"


def build_task_prompt(prompt: str, task: str, source_files: list[str]) -> str:
    """Build the full user-facing task prompt."""
    if prompt.strip():
        return f"{prompt}\n\nSource files:\n{_source_file_list(source_files)}\nTask: {task}"
    return f"Source files:\n{_source_file_list(source_files)}\nTask: {task}"


def _graph_input(task: str, source_files: list[str]) -> TabularTaskInput:
    """Build the validated graph input payload."""
    return TabularTaskInput(task=task, source_files=source_files)


def _extracted_targets(extraction_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def _final_answer_payload(state: TabularTaskState) -> dict[str, Any]:
    """Build the structured payload for final answer generation."""
    return {
        "task": state.task,
        "status": state.status,
        "source_files": state.source_files,
        "database_path": state.database_path,
        "extracted_targets": state.extracted_targets,
        "selected_targets": state.selected_targets,
        "candidate_sql": state.candidate_sql,
        "sql_result": state.sql_result,
        "saved_view_name": state.saved_view_name,
        "last_error": state.last_error,
    }


def make_extract_node(tabular_tools: list[Any]):
    """Create the extraction node from the tabular tool surface."""
    extract_tabular = _tool_by_name(tabular_tools, "extract_tabular")

    def extract_node(state: TabularTaskState) -> dict[str, Any]:
        extraction_results: list[dict[str, Any]] = []
        database_paths: set[str] = set()
        for source_file in state.source_files:
            extraction = extract_tabular.invoke({"path": source_file})
            extraction_results.append(extraction)
            if extraction.get("status") != "loaded":
                return {
                    "status": "error",
                    "extraction_results": extraction_results,
                    "last_error": extraction.get("message", f"Extraction failed for {source_file}"),
                    "trace": _append_trace(state, f"extract failed for {source_file}"),
                }
            if database_path := extraction.get("database_path"):
                database_paths.add(str(database_path))

        if len(database_paths) != 1:
            return {
                "status": "error",
                "extraction_results": extraction_results,
                "last_error": "Expected one shared SQLite database path after extraction.",
                "trace": _append_trace(state, "extract produced inconsistent database paths"),
            }

        database_path = next(iter(database_paths))
        extracted_targets = _extracted_targets(extraction_results)
        return {
            "status": "extracted",
            "database_path": database_path,
            "extraction_results": extraction_results,
            "extracted_targets": extracted_targets,
            "trace": _append_trace(state, f"extracted {len(extracted_targets)} targets into {database_path}"),
        }

    return extract_node


def make_sql_node(*, llm: Any):
    """Create the SQL-agent node."""

    def sql_node(state: TabularTaskState) -> dict[str, Any]:
        if not state.database_path:
            return {
                "status": "error",
                "last_error": "No SQLite database path was available for SQL analysis.",
                "trace": _append_trace(state, "sql skipped because database_path was missing"),
            }

        stale_view_name = _suggest_view_name(state.task)
        with sqlite3.connect(state.database_path) as connection:
            connection.execute(f'DROP VIEW IF EXISTS "{stale_view_name}"')
            connection.commit()

        sql_output = answer_sql_question(
            state.task,
            llm=llm,
            database_path=state.database_path,
        )
        trace_message = f"sql agent finished with status={sql_output.status}"
        if sql_output.selected_targets:
            trace_message += f" on {', '.join(sql_output.selected_targets)}"
        else:
            trace_message += f" after clearing stale view {stale_view_name}"
        return {
            "status": sql_output.status,
            "sql_agent_output": sql_output.model_dump(mode="json"),
            "selected_targets": sql_output.selected_targets,
            "candidate_sql": sql_output.candidate_sql,
            "sql_result": sql_output.result,
            "last_error": None if sql_output.status == "complete" else sql_output.last_error,
            "trace": _append_trace(state, trace_message),
        }

    return sql_node


def save_node(state: TabularTaskState) -> dict[str, Any]:
    """Save the final SQL query result as a reusable SQLite view."""
    if not state.candidate_sql or not state.database_path:
        return {
            "status": "error",
            "last_error": "No executable SQL was available to save as a view.",
            "trace": _append_trace(state, "save skipped because candidate_sql was missing"),
        }

    view_name = _suggest_view_name(state.task)
    saved_view = save_view(
        state.candidate_sql,
        view_name,
        database_path=state.database_path,
        replace=True,
    )
    if saved_view.get("status") != "ok":
        return {
            "status": "error",
            "saved_view_name": view_name,
            "saved_view": saved_view,
            "last_error": saved_view.get("message", f"Failed to save view {view_name}"),
            "trace": _append_trace(state, f"save failed for view {view_name}"),
        }

    return {
        "status": "saved",
        "saved_view_name": view_name,
        "saved_view": saved_view,
        "last_error": None,
        "trace": _append_trace(state, f"saved result as view {view_name}"),
    }


def make_answer_node(llm: Any, *, prompt: str):
    """Create the final answer composition node."""

    def answer_node(state: TabularTaskState) -> dict[str, Any]:
        response = llm.invoke(
            [
                SystemMessage(content=FINAL_ANSWER_SYSTEM_PROMPT),
                HumanMessage(
                    content=(
                        f"{build_task_prompt(prompt, state.task, state.source_files)}\n\n"
                        f"Execution result:\n{json.dumps(_final_answer_payload(state), ensure_ascii=True, sort_keys=True)}"
                    )
                ),
            ]
        )
        return {
            "final_answer": getattr(response, "content", str(response)),
            "trace": _append_trace(state, "generated final answer"),
        }

    return answer_node


def _route_after_extract(state: TabularTaskState) -> str:
    """Route after extraction."""
    return "sql" if state.status == "extracted" else "answer"


def _route_after_sql(state: TabularTaskState) -> str:
    """Route after SQL analysis."""
    return "save" if state.status == "complete" and bool(state.candidate_sql) else "answer"


def create_tabular_graph(*, llm: Any, prompt: str = "", root_dir: str | Path | None = None) -> CompiledStateGraph:
    """Create the deterministic tabular-to-SQL graph for the billing app."""
    tabular_tools = make_tabular_tools(root_dir=root_dir)
    builder = StateGraph(
        TabularTaskState,
        input_schema=TabularTaskInput,
        output_schema=TabularTaskOutput,
    )
    builder.add_node("extract", make_extract_node(tabular_tools))
    builder.add_node("sql", make_sql_node(llm=llm))
    builder.add_node("save", save_node)
    builder.add_node("answer", make_answer_node(llm, prompt=prompt))

    builder.add_edge(START, "extract")
    builder.add_conditional_edges(
        "extract",
        _route_after_extract,
        {
            "sql": "sql",
            "answer": "answer",
        },
    )
    builder.add_conditional_edges(
        "sql",
        _route_after_sql,
        {
            "save": "save",
            "answer": "answer",
        },
    )
    builder.add_edge("save", "answer")
    builder.add_edge("answer", END)
    return builder.compile()


class TabularTaskAgent:
    """Deterministic tabular analysis agent with a pinned save-view step."""

    def __init__(
        self,
        *,
        prompt: str = "",
        root_dir: str | Path | None = None,
    ):
        self.prompt = prompt
        self.model = os.getenv(DEFAULT_MODEL_ENV) or DEFAULT_MODEL
        self.llm = ChatOpenAI(
            model=self.model,
            temperature=0,
            reasoning_effort=DEFAULT_REASONING_EFFORT,
        )
        self.graph = create_tabular_graph(llm=self.llm, prompt=prompt, root_dir=root_dir)

    def invoke(self, task: str, *, source_files: list[str]) -> TabularTaskOutput:
        """Run the graph once and validate the final output."""
        result = self.graph.invoke(_graph_input(task, source_files))
        return TabularTaskOutput.model_validate(result)


def _render_step_update(step_name: str, update: dict[str, Any]) -> str:
    """Render one streamed graph update compactly."""
    if step_name == "extract":
        return json.dumps(
            {
                "status": update.get("status"),
                "database_path": update.get("database_path"),
                "extracted_targets": update.get("extracted_targets", []),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    if step_name == "sql":
        return json.dumps(
            {
                "status": update.get("status"),
                "selected_targets": update.get("selected_targets", []),
                "candidate_sql": update.get("candidate_sql"),
                "last_error": update.get("last_error"),
                "sql_result": update.get("sql_result"),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    if step_name == "save":
        return json.dumps(
            {
                "status": update.get("status"),
                "saved_view_name": update.get("saved_view_name"),
                "last_error": update.get("last_error"),
            },
            ensure_ascii=True,
            sort_keys=True,
        )
    if step_name == "answer":
        return str(update.get("final_answer", "")).strip()
    return json.dumps(update, ensure_ascii=True, sort_keys=True, default=str)


def run_task(
    *,
    prompt: str = "",
    task: str,
    source_files: list[str],
    root_dir: str | Path | None = None,
) -> TabularTaskOutput:
    """Run the deterministic graph and stream step updates."""
    agent = TabularTaskAgent(
        prompt=prompt,
        root_dir=root_dir,
    )
    graph_input = _graph_input(task, source_files)
    print("\n[human]")
    print(f"Source files:\n{_source_file_list(source_files)}\nTask: {task}")

    final_state: dict[str, Any] | None = None
    for chunk in agent.graph.stream(
        graph_input,
        stream_mode=["updates", "values"],
        version="v2",
    ):
        chunk_type = chunk.get("type")
        if chunk_type == "updates":
            for step_name, update in chunk.get("data", {}).items():
                if step_name.startswith("__") or not isinstance(update, dict):
                    continue
                print(f"\n[step:{step_name}]")
                print(_render_step_update(step_name, update))
        elif chunk_type == "values":
            final_state = chunk.get("data")

    if final_state is None:
        raise RuntimeError("Graph completed without a final state.")

    output = TabularTaskOutput.model_validate(final_state)
    print("\n[final answer]")
    print(output.final_answer or "")
    return output
