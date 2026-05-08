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
    selected_sql_artifacts: list[str] = Field(default_factory=list, description="Allowed SQL artifacts used by the draft.")


class SQLRuntimeRepair(BaseModel):
    """Hashline edits for repairing SQLite execution errors."""

    edits: list[HashlineEdit] = Field(default_factory=list, description="Hashline edits to apply to the current SQL file.")


class ExistingSQLDecision(BaseModel):
    """Decision about whether an existing SQL artifact should guide the query stage."""

    reuse_existing_sql: bool = Field(default=False, description="Whether one related existing SQL artifact is already ready for this request.")
    use_as_draft_context: bool = Field(default=False, description="Whether one related SQL artifact should be used as the starting point for write_sql.")
    sql_path: str | None = Field(default=None, description="Exact sql_path of the related SQL artifact to execute or use as draft context.")
    reason: str = Field(default="", description="Short reason for reusing, adapting, or redrafting from zero.")


class QueryStageState(
    OrchestratorInput,
    PreparedDataState,
    SQLArtifactState,
    SQLRuntimeState,
):
    """Internal graph state for the file-backed query stage."""


DraftFn = Callable[[QueryStageState], SQLDraft]
RuntimeRepairFn = Callable[[QueryStageState], SQLRuntimeRepair]
ExistingSQLSelectorFn = Callable[[QueryStageState], ExistingSQLDecision]
