"""State models for the query stage."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import BaseModel, Field

from ...tools.fs import HashlineEdit
from ..orchestrator.state import (
    OrchestratorInput,
    PreparedDataState,
    SQLArtifactState,
    SQLRuntimeState,
)


class SQLDraft(BaseModel):
    """Structured SQL draft output."""

    sql: str = Field(description="Read-only SQL query to write into the SQL artifact file.")
    filename_hint: str | None = Field(default=None, description="Short noun-focused filename hint for the SQL artifact.")
    selected_targets: list[str] = Field(default_factory=list, description="Allowed targets used by the draft.")


class SQLRuntimeRepair(BaseModel):
    """Hashline edits for repairing SQLite execution errors."""

    edits: list[HashlineEdit] = Field(default_factory=list, description="Hashline edits to apply to the current SQL file.")


class QueryStageState(
    OrchestratorInput,
    PreparedDataState,
    SQLArtifactState,
    SQLRuntimeState,
):
    """Internal graph state for the file-backed query stage."""


DraftFn = Callable[[QueryStageState], SQLDraft]
RuntimeRepairFn = Callable[[QueryStageState], SQLRuntimeRepair]
