"""Request schemas for the workbench API."""

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Request body for the chat endpoint."""

    message: str = Field(min_length=1)


class SqlRunRequest(BaseModel):
    """Request body for read-only SQL execution."""

    sql: str = Field(min_length=1)
    max_rows: int = Field(default=100, ge=1, le=500)


class SkillSaveRequest(BaseModel):
    """Request body for saving skill editor content."""

    name: str = Field(min_length=1)
    content: str = ""
