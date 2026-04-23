"""State models for the prep agent."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MAX_TRACE_MESSAGES = 12


class PrepTaskInput(BaseModel):
    """Public input for the prep agent."""

    task: str
    source_files: list[str]
    worker_instructions: str = ""
    skill_refs: list[dict[str, Any]] = Field(default_factory=list)
    max_prep_trials: int = 2


class PrepTaskOutput(BaseModel):
    """Public output for the prep agent."""

    status: str = "pending"
    database_path: str | None = None
    extraction_results: list[dict[str, Any]] = Field(default_factory=list)
    extracted_targets: list[dict[str, Any]] = Field(default_factory=list)
    last_error: str | None = None
    prep_attempts: int = 0
    trace: list[str] = Field(default_factory=list)


class PrepAgentDecision(BaseModel):
    """Structured summary returned after the prep agent finishes."""

    status: Literal["prepared", "retry", "blocked", "error"]
    summary: str
    retry_instructions: list[str] = Field(default_factory=list)
    last_error: str | None = None


def append_trace(trace: list[str], message: str) -> list[str]:
    """Append one trace message and keep the trace bounded."""
    return [*trace, message][-MAX_TRACE_MESSAGES:]
