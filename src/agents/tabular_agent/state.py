"""State models for the deterministic tabular analysis workflow."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
    matched_skill_names: list[str] = Field(default_factory=list)
    search_context: str = ""
    sql_agent_output: dict[str, Any] | None = None


def append_trace(state: TabularTaskState, message: str) -> list[str]:
    """Append one trace message."""
    return [*state.trace, message]


def build_graph_input(task: str, source_files: list[str]) -> TabularTaskInput:
    """Build the validated graph input payload."""
    return TabularTaskInput(task=task, source_files=source_files)
