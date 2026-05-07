"""Structured response schema for the prep stage."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PrepStageDecision(BaseModel):
    """Structured summary returned after the prep stage finishes."""

    status: Literal["prepared", "retry", "blocked", "error"] = Field(description="Prep-stage decision after the tool-using loop finishes.")
    summary: str = Field(default="", description="Brief human-readable summary of the prep-stage outcome.")
    retry_instructions: list[str] = Field(default_factory=list, description="Instructions for another prep attempt when retrying is useful.")
    last_error: str | None = Field(default=None, description="Most relevant prep-stage error, when one occurred.")


class PrepStageOutput(BaseModel):
    """Normalized prep result returned to orchestrator-owned workflows."""

    status: Literal["pending", "prepared", "error"] = Field(default="pending", description="Terminal prep-stage status.")
    database_path: str | None = Field(default=None, description="SQLite database path produced by successful extraction.")
    extraction_results: list[dict[str, Any]] = Field(default_factory=list, description="Raw extraction tool results observed during prep.")
    extracted_sql_artifacts: list[dict[str, Any]] = Field(default_factory=list, description="SQL-ready artifacts collected from extraction results.")
    last_error: str | None = Field(default=None, description="Most relevant prep-stage error, when one occurred.")
    prep_attempts: int = Field(default=0, description="Number of prep attempts used to reach this output.")
    trace: list[str] = Field(default_factory=list, description="Compact prep-stage trace messages.")
