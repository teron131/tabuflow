"""Structured response schema for the prep agent."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PrepAgentDecision(BaseModel):
    """Structured summary returned after the prep agent finishes."""

    status: Literal["prepared", "retry", "blocked", "error"]
    summary: str = ""
    retry_instructions: list[str] = Field(default_factory=list)
    last_error: str | None = None
