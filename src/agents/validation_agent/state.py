"""State models for the validation agent."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ValidationInput(BaseModel):
    """Public input schema for one validation graph run."""

    task: str
    source_files: list[str]
    extracted_targets: list[dict[str, Any]] = Field(default_factory=list)
    selected_targets: list[str] = Field(default_factory=list)
    candidate_sql: str | None = None
    sql_result: dict[str, Any] | None = None
    previous_feedback: dict[str, Any] | None = None
    validation_attempts: int = 0


class ValidationOutput(BaseModel):
    """Structured validation result for SQL output."""

    valid: bool = Field(default=False, description="Whether the SQL result appears to satisfy the task.")
    retryable: bool = Field(default=True, description="Whether another SQL attempt is likely to help.")
    summary: str = Field(default="", description="Short explanation of the validation judgment.")
    instructions: list[str] = Field(default_factory=list, description="Concrete guidance for the next SQL attempt when retryable.")
