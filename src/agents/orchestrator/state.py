"""State and public schemas for the orchestrator graph."""

from typing import Annotated, Any
from uuid import uuid4

from langchain_core.messages import AnyMessage, BaseMessage, HumanMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class PreparedDataState(BaseModel):
    """Prepared database context consumed by query-stage tools."""

    run_id: str = Field(default_factory=lambda: uuid4().hex[:8], description="Short workflow run identifier used for generated artifacts.")
    database_path: str | None = Field(default=None, description="SQLite database path prepared for SQL execution.")
    preferred_sql_artifacts: list[str] = Field(default_factory=list, description="Preferred SQL artifact names selected by prep.")
    extracted_sql_artifacts: list[dict[str, Any]] = Field(default_factory=list, description="Prepared table or view metadata available to SQL drafting.")
    worker_context: str = Field(default="", description="Worker-facing context assembled from harness prompt and matched skills.")
    skill_refs: list[dict[str, Any]] = Field(default_factory=list, description="Loaded skill reference payloads relevant to this run.")


class SQLArtifactState(BaseModel):
    """Current SQL artifact, execution, and validation state."""

    status: str = Field(default="pending", description="Current SQL-stage status.")
    sql_path: str | None = Field(default=None, description="Path to the current SQL artifact file.")
    reuse_existing_sql: bool = Field(default=False, description="Whether the current SQL stage should execute an accepted existing SQL artifact.")
    related_sql_artifacts: list[dict[str, Any]] = Field(default_factory=list, description="Existing SQL artifacts considered for this request.")
    selected_sql_artifacts: list[str] = Field(default_factory=list, description="SQL artifacts selected by the current draft.")
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

    messages: Annotated[list[AnyMessage], add_messages] = Field(default_factory=list, description="Conversation spine for graph runs.")
    source_files: list[str] = Field(default_factory=list, description="Declared source files provided with the message.")
    max_validation_retries: int = Field(default=2, description="Maximum semantic validation retry count for the SQL stage.")


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

    skills_overview: str = Field(default="", description="Listed workspace skills available to the public chat orchestrator.")
    structured_response: Any | None = Field(default=None, description="Structured response produced by a create_agent subgraph.")
    prep_output: dict[str, Any] | None = Field(default=None, description="Serialized prep-stage output artifact.")


def _content_block_text(block: Any) -> str:
    """Return readable text for one multimodal content block."""
    if isinstance(block, str):
        return block.strip()
    if not isinstance(block, dict):
        return ""
    if block.get("type") == "text":
        return str(block.get("text") or "").strip()
    if block.get("type") in {"image", "image_url"}:
        return "[image]"
    return str(block.get("text") or "").strip()


def message_text(message: BaseMessage | dict[str, Any]) -> str:
    """Return compact readable text for one chat message."""
    if isinstance(message, BaseMessage):
        if isinstance(message.content, str):
            return message.content.strip()
        return "\n".join(text for text in (_content_block_text(block) for block in message.content) if text).strip()
    content = message.get("content")
    if isinstance(content, list):
        return "\n".join(text for text in (_content_block_text(block) for block in content) if text).strip()
    return str(content or "").strip()


def latest_user_message(messages: list[AnyMessage]) -> str:
    """Return the latest user-authored message text from chat state."""
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and message.name == "user":
            return message_text(message)
        if isinstance(message, dict) and message.get("role") in {"user", "human"} and message.get("name") == "user":
            return message_text(message)
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and message.name != "prep_stage":
            return message_text(message)
        if isinstance(message, dict) and message.get("role") in {"user", "human"} and message.get("name") != "prep_stage":
            return message_text(message)
    return ""
