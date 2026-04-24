"""State models for the standalone SQL agent."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field


class SQLPlan(BaseModel):
    """Structured SQL planning output."""

    ready: bool = Field(description="Whether the planner is confident enough to run SQL.")
    sql: str | None = Field(default=None, description="Read-only SQL query to execute when ready is true.")
    selected_targets: list[str] = Field(default_factory=list, description="Target tables or views used by the plan.")
    rationale: str = Field(default="", description="Short reasoning for the chosen SQL.")
    blocking_reason: str | None = Field(default=None, description="Why planning could not safely proceed.")


class SQLAgentInput(BaseModel):
    """Public input schema for the SQL agent graph."""

    question: str
    database_path: str | None = None
    preferred_targets: list[str] = Field(default_factory=list)
    source_files: list[str] = Field(default_factory=list)
    worker_context: str = ""
    skill_refs: list[dict[str, Any]] = Field(default_factory=list)
    validation_feedback: dict[str, Any] | None = None
    max_suggestions: int = 3
    max_repairs: int = 2
    sample_rows: int = 3
    text_value_hints: int = 3


class SQLAgentOutput(BaseModel):
    """Public output schema for the SQL agent graph."""

    status: str = "pending"
    selected_targets: list[str] = Field(default_factory=list)
    candidate_sql: str | None = None
    repair_hints: list[dict[str, Any]] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    attempts: int = 0
    rationale: str | None = None
    last_error: str | None = None
    trace: list[str] = Field(default_factory=list)


class SQLAgentState(SQLAgentInput, SQLAgentOutput):
    """Internal graph state for the SQL workflow."""

    suggestions: list[dict[str, Any]] = Field(default_factory=list)
    inspected_targets: list[dict[str, Any]] = Field(default_factory=list)
    plan: SQLPlan | None = None
    repair_count: int = 0


PlannerFn = Callable[[SQLAgentState], SQLPlan]
