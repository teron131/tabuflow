"""Pydantic schemas for public email inspection payloads."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class EmailPayload(BaseModel):
    """Base class for email tool payload schemas."""

    model_config = ConfigDict(extra="forbid")


class EmailSourcePayload(EmailPayload):
    """Base payload tied to one source email file."""

    path: str = Field(description="Resolved source email path.")


class EmailInspectionResult(EmailSourcePayload):
    """Public response returned by email inspection."""

    status: Literal["ok"] = Field(description="Inspection status.")
    format: Literal["eml", "msg"] = Field(description="Parsed email file format.")
    subject: str = Field(description="Email subject.")
    sender: str = Field(description="Email sender.")
    recipients: str = Field(description="Email recipients.")
    cc: str = Field(description="Email CC recipients.")
    sent_at: str | None = Field(description="Email sent timestamp when available.")
    body_source: str = Field(description="Source body representation used for the preview.")
    body_preview: str = Field(description="Bounded plain-text body preview.")
    body_char_count: int = Field(ge=0, description="Full normalized body character count.")
    attachments: list[str] = Field(description="Attachment filename preview.")
    reference_only: bool = Field(description="Whether this payload is reference context only.")
    summary: str = Field(description="Compact human-readable inspection summary.")


def dump_email_inspection_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and return a JSON-shaped email inspection payload."""
    return EmailInspectionResult.model_validate(payload).model_dump(mode="json")
