"""Structured response schema for the prep_pdf stage."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PrepPdfDecision(BaseModel):
    """Structured summary returned after the prep_pdf stage finishes."""

    status: Literal["prepared", "retry", "blocked", "error"] = Field(description="prep_pdf decision after the tool-using loop finishes.")
    summary: str = Field(default="", description="Brief human-readable summary of the prep_pdf stage outcome.")
    retry_instructions: list[str] = Field(default_factory=list, description="Instructions for another prep_pdf attempt when retrying is useful.")
    last_error: str | None = Field(default=None, description="Most relevant prep_pdf stage error, when one occurred.")


class PrepPdfOutput(BaseModel):
    """Normalized prep_pdf result returned to orchestrator-owned workflows."""

    status: Literal["pending", "prepared", "error"] = Field(default="pending", description="Terminal prep_pdf stage status.")
    database_path: str | None = Field(default=None, description="SQLite database path produced by successful extraction.")
    extracted_sql_artifacts: list[dict[str, object]] = Field(default_factory=list, description="SQL-ready artifacts collected from extraction results.")
    last_error: str | None = Field(default=None, description="Most relevant prep_pdf stage error, when one occurred.")
    prep_attempts: int = Field(default=0, description="Number of prep_pdf attempts used to reach this output.")
    trace: list[str] = Field(default_factory=list, description="Compact prep_pdf stage trace messages.")
