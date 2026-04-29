"""Request schemas for the workbench API."""

from typing import Literal

from pydantic import BaseModel, Field


class ChatHistoryMessage(BaseModel):
    """One browser-visible message passed through the chat bridge."""

    role: Literal["user", "assistant", "system"]
    content: str = ""


class ChatRequest(BaseModel):
    """Request body for the chat endpoint."""

    message: str = Field(min_length=1)
    messages: list[ChatHistoryMessage] = Field(default_factory=list)
    model: str | None = None
    source_files: list[str] = Field(default_factory=list)


class SqlRunRequest(BaseModel):
    """Request body for read-only SQL execution."""

    sql: str = Field(min_length=1)
    max_rows: int = Field(default=100, ge=1, le=500)


class SkillSaveRequest(BaseModel):
    """Request body for saving skill editor content."""

    name: str = Field(min_length=1)
    content: str = ""
