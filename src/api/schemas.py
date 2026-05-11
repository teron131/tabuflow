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
    max_rows: int = Field(default=250, ge=1, le=500)


class SourcePreviewRequest(BaseModel):
    """Request body for bounded raw source previews."""

    path: str = Field(min_length=1)
    start_row: int = Field(default=1, ge=1)
    max_rows: int = Field(default=250, ge=1, le=500)
    sheet: str | None = None


class FileExplanationRequest(BaseModel):
    """Request body for model-backed file explanations."""

    path: str = Field(min_length=1)
    force: bool = False
    model: str | None = None


class SkillSaveRequest(BaseModel):
    """Request body for saving skill editor content."""

    name: str = Field(min_length=1)
    content: str = ""


class SkillCreateRequest(BaseModel):
    """Request body for creating a skill package frame."""

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    reference_files: list[str] = Field(default_factory=list)
    script_files: list[str] = Field(default_factory=list)


class SkillResourceSaveRequest(BaseModel):
    """Request body for saving a workspace skill resource file."""

    path: str = Field(min_length=1)
    content: str = ""
