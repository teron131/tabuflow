"""State models for the orchestrator-owned SQL stage."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from ....tools.fs import HashlineEdit


class SQLDraft(BaseModel):
    """Structured SQL draft output."""

    sql: str = Field(description="Read-only SQL query to write into the SQL artifact file.")
    filename_hint: str | None = Field(default=None, description="Short noun-focused filename hint for the SQL artifact.")
    selected_targets: list[str] = Field(default_factory=list, description="Allowed targets used by the draft.")


class SQLRuntimeRepair(BaseModel):
    """Hashline edits for repairing SQLite execution errors."""

    edits: list[HashlineEdit] = Field(default_factory=list, description="Hashline edits to apply to the current SQL file.")


class TaskInput(BaseModel):
    """User task fields shared by orchestration and SQL-stage schemas."""

    task: str
    source_files: list[str] = Field(default_factory=list)


class SQLStageContext(BaseModel):
    """Context fields consumed by SQL-stage nodes."""

    run_id: str = Field(default_factory=lambda: uuid4().hex[:8])
    database_path: str | None = None
    sql_path: str | None = None
    preferred_targets: list[str] = Field(default_factory=list)
    extracted_targets: list[dict[str, Any]] = Field(default_factory=list)
    worker_context: str = ""
    skill_refs: list[dict[str, Any]] = Field(default_factory=list)
    validation_feedback: dict[str, Any] | None = None
    max_repairs: int = 2


class SQLStageOutput(BaseModel):
    """Output fields produced by the orchestrator-owned SQL stage nodes."""

    status: str = "pending"
    sql_path: str | None = None
    selected_targets: list[str] = Field(default_factory=list)
    candidate_sql: str | None = None
    repair_hints: list[dict[str, Any]] = Field(default_factory=list)
    result: dict[str, Any] | None = None
    attempts: int = 0
    last_error: str | None = None
    trace: list[str] = Field(default_factory=list)


class SQLStageRuntimeState(BaseModel):
    """Transient state used inside SQL runtime-repair loops."""

    sql_hashlines: str | None = None
    repair_count: int = 0


class SQLStageState(
    TaskInput,
    SQLStageContext,
    SQLStageOutput,
    SQLStageRuntimeState,
):
    """Internal graph state for the file-backed SQL stage."""


DraftFn = Callable[[SQLStageState], SQLDraft]
RuntimeRepairFn = Callable[[SQLStageState], SQLRuntimeRepair]
