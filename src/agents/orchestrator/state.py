"""State and public schemas for the orchestrator graph."""

from typing import Annotated, Any
from uuid import uuid4

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field, field_validator


class PreparedDataState(BaseModel):
    """Prepared database context consumed by query-stage tools."""

    run_id: str = Field(default_factory=lambda: uuid4().hex[:8], description="Short workflow run identifier used for generated artifacts.")
    database_path: str | None = Field(default=None, description="SQLite database path prepared for SQL execution.")
    preferred_targets: list[str] = Field(default_factory=list, description="Preferred SQL target names selected by prep.")
    extracted_targets: list[dict[str, Any]] = Field(default_factory=list, description="Prepared table or view metadata available to SQL drafting.")
    worker_context: str = Field(default="", description="Worker-facing context assembled from harness prompt and matched skills.")
    skill_refs: list[dict[str, Any]] = Field(default_factory=list, description="Loaded skill reference payloads relevant to this run.")


class SQLArtifactState(BaseModel):
    """Current SQL artifact, execution, and validation state."""

    status: str = Field(default="pending", description="Current SQL-stage status.")
    sql_path: str | None = Field(default=None, description="Path to the current SQL artifact file.")
    selected_targets: list[str] = Field(default_factory=list, description="SQL targets selected by the current draft.")
    candidate_sql: str | None = Field(default=None, description="Current SQL text read from or written to the SQL artifact.")
    repair_hints: list[dict[str, Any]] = Field(default_factory=list, description="Deterministic hints for repairing SQLite runtime errors.")
    result: dict[str, Any] | None = Field(default=None, description="SQLite execution result payload.")
    attempts: int = Field(default=0, description="Number of SQL execution attempts made in the current loop.")
    last_error: str | None = Field(default=None, description="Most recent SQL-stage error message.")
    trace: list[str] = Field(default_factory=list, description="Compact SQL-stage trace messages.")
    validation_feedback: dict[str, Any] | None = Field(default=None, description="Semantic validation feedback for another SQL draft.")
    validation_attempts: int = Field(default=0, description="Number of semantic validation retry requests made so far.")


class SQLRuntimeState(BaseModel):
    """Transient state used inside SQL runtime-repair loops."""

    max_repairs: int = Field(default=2, description="Maximum SQLite runtime-repair attempts before validation/finalization.")
    sql_hashlines: str | None = Field(default=None, description="Hashline view of the current SQL artifact for targeted repair.")
    repair_count: int = Field(default=0, description="Number of runtime-repair passes already attempted.")


class OrchestratorInput(BaseModel):
    """Public input schema for the orchestrator workflow."""

    message: str = Field(description="Raw user chat message that started the turn.")
    source_files: list[str] = Field(default_factory=list, description="Declared source files provided with the message.")
    max_validation_retries: int = Field(default=2, description="Maximum semantic validation retry count for the SQL stage.")

    @field_validator("message")
    @classmethod
    def require_message(cls, value: str) -> str:
        """Reject blank chat messages."""
        message = value.strip()
        if not message:
            raise ValueError("message must not be blank.")
        return message


class OrchestratorOutput(BaseModel):
    """Public output schema for the orchestrator workflow."""

    content: str = Field(default="", description="Final assistant-facing response content.")
    artifact: dict[str, Any] = Field(default_factory=dict, description="Compact terminal workflow artifact.")
    stage_artifacts: dict[str, dict[str, Any]] = Field(default_factory=dict, description="Stage artifacts keyed by stage name.")


class OrchestratorState(
    OrchestratorInput,
    OrchestratorOutput,
    PreparedDataState,
    SQLArtifactState,
    SQLRuntimeState,
):
    """Parent graph state shared by the orchestrator-owned workflow stages."""

    messages: Annotated[list[AnyMessage], add_messages] = Field(default_factory=list, description="Conversation spine and stage-report log for graph runs.")
    structured_response: Any | None = Field(default=None, description="Structured response produced by a create_agent subgraph.")
    prep_output: dict[str, Any] | None = Field(default=None, description="Serialized prep-stage output artifact.")
