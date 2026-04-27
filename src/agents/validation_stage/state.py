"""State models for the validation stage."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class ValidationInput(BaseModel):
    """Public input schema for one validation graph run."""

    message: str = Field(description="Raw chat message the SQL result should satisfy.")
    source_files: list[str] = Field(default_factory=list, description="Declared source files associated with the workflow turn.")
    extracted_targets: list[dict[str, Any]] = Field(default_factory=list, description="Prepared target metadata available to the SQL stage.")
    selected_targets: list[str] = Field(default_factory=list, description="SQL targets selected by the candidate query.")
    candidate_sql: str | None = Field(default=None, description="Candidate SQL text being validated.")
    sql_result: dict[str, Any] | None = Field(default=None, description="SQLite execution result payload being validated.")
    previous_feedback: dict[str, Any] | None = Field(default=None, description="Prior validation feedback, if this is a retry.")
    validation_attempts: int = Field(default=0, description="Number of prior semantic validation retry requests.")

    @field_validator("message")
    @classmethod
    def require_message(cls, value: str) -> str:
        """Reject blank validation requests."""
        message = value.strip()
        if not message:
            raise ValueError("message must not be blank.")
        return message


class ValidationOutput(BaseModel):
    """Structured validation result for SQL output."""

    valid: bool = Field(default=False, description="Whether the SQL result appears to satisfy the message.")
    retryable: bool = Field(default=True, description="Whether another SQL attempt is likely to help.")
    summary: str = Field(default="", description="Short explanation of the validation judgment.")
    instructions: list[str] = Field(default_factory=list, description="Concrete guidance for the next SQL attempt when retryable.")
