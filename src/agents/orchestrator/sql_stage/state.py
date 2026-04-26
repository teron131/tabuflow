"""State models for the orchestrator-owned SQL stage."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from ....tools.fs import HashlineEdit


class SQLDraft(BaseModel):
    """Structured SQL draft output."""

    sql: str = Field(description="Read-only SQL query to write into the SQL artifact file.")
    filename_hint: str | None = Field(default=None, description="Short noun-focused filename hint for the SQL artifact.")
    selected_targets: list[str] = Field(default_factory=list, description="Allowed targets used by the draft.")


class SQLRuntimeRepair(BaseModel):
    """Hashline edits for repairing SQLite execution errors."""

    edits: list[HashlineEdit] = Field(default_factory=list, description="Hashline edits to apply to the current SQL file.")


class MessageInput(BaseModel):
    """Chat message fields shared by orchestration and SQL-stage schemas."""

    message: str = Field(description="Raw chat message that started the workflow turn.")
    source_files: list[str] = Field(default_factory=list, description="Declared source files provided with the message.")

    @field_validator("message")
    @classmethod
    def require_message(cls, value: str) -> str:
        """Reject blank workflow requests."""
        message = value.strip()
        if not message:
            raise ValueError("message must not be blank.")
        return message


class SQLStageContext(BaseModel):
    """Context fields consumed by SQL-stage nodes."""

    run_id: str = Field(default_factory=lambda: uuid4().hex[:8], description="Short workflow run identifier used for generated artifacts.")
    database_path: str | None = Field(default=None, description="SQLite database path prepared for SQL execution.")
    sql_path: str | None = Field(default=None, description="Path to the current SQL artifact file.")
    preferred_targets: list[str] = Field(default_factory=list, description="Preferred SQL target names selected by prep.")
    extracted_targets: list[dict[str, Any]] = Field(default_factory=list, description="Prepared table or view metadata available to SQL drafting.")
    worker_context: str = Field(default="", description="Worker-facing context assembled from harness prompt and matched skills.")
    skill_refs: list[dict[str, Any]] = Field(default_factory=list, description="Loaded skill reference payloads relevant to this run.")
    validation_feedback: dict[str, Any] | None = Field(default=None, description="Semantic validation feedback for another SQL draft.")
    max_repairs: int = Field(default=2, description="Maximum SQLite runtime-repair attempts before validation/finalization.")


class SQLStageOutput(BaseModel):
    """Output fields produced by the orchestrator-owned SQL stage nodes."""

    status: str = Field(default="pending", description="Current SQL-stage status.")
    sql_path: str | None = Field(default=None, description="Path to the SQL artifact used for the current output.")
    selected_targets: list[str] = Field(default_factory=list, description="SQL targets selected by the current draft.")
    candidate_sql: str | None = Field(default=None, description="Current SQL text read from or written to the SQL artifact.")
    repair_hints: list[dict[str, Any]] = Field(default_factory=list, description="Deterministic hints for repairing SQLite runtime errors.")
    result: dict[str, Any] | None = Field(default=None, description="SQLite execution result payload.")
    attempts: int = Field(default=0, description="Number of SQL execution attempts made in the current loop.")
    last_error: str | None = Field(default=None, description="Most recent SQL-stage error message.")
    trace: list[str] = Field(default_factory=list, description="Compact SQL-stage trace messages.")


class SQLStageRuntimeState(BaseModel):
    """Transient state used inside SQL runtime-repair loops."""

    sql_hashlines: str | None = Field(default=None, description="Hashline view of the current SQL artifact for targeted repair.")
    repair_count: int = Field(default=0, description="Number of runtime-repair passes already attempted.")


class SQLStageState(
    MessageInput,
    SQLStageContext,
    SQLStageOutput,
    SQLStageRuntimeState,
):
    """Internal graph state for the file-backed SQL stage."""


DraftFn = Callable[[SQLStageState], SQLDraft]
RuntimeRepairFn = Callable[[SQLStageState], SQLRuntimeRepair]
