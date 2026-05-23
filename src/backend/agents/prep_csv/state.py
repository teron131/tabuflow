"""Structured response schema for the prep_csv stage."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PrepCsvDecision(BaseModel):
    """Structured summary returned after the prep_csv stage finishes."""

    status: Literal["prepared", "retry", "blocked", "error"] = Field(description="prep_csv decision after the tool-using loop finishes.")
    summary: str = Field(default="", description="Brief human-readable summary of the prep_csv stage outcome.")
    retry_instructions: list[str] = Field(default_factory=list, description="Instructions for another prep_csv attempt when retrying is useful.")
    last_error: str | None = Field(default=None, description="Most relevant prep_csv stage error, when one occurred.")


class PrepCsvOutput(BaseModel):
    """Normalized prep_csv result returned to orchestrator-owned workflows."""

    status: Literal["pending", "prepared", "error"] = Field(default="pending", description="Terminal prep_csv stage status.")
    database_path: str | None = Field(default=None, description="SQLite database path produced by successful extraction.")
    extracted_sql_artifacts: list[dict[str, object]] = Field(default_factory=list, description="SQL-ready artifacts collected from extraction results.")
    last_error: str | None = Field(default=None, description="Most relevant prep_csv stage error, when one occurred.")
    prep_attempts: int = Field(default=0, description="Number of prep_csv attempts used to reach this output.")
    trace: list[str] = Field(default_factory=list, description="Compact prep_csv stage trace messages.")
