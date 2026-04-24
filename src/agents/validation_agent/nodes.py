"""Validation graph helpers for SQL-agent output review."""

from __future__ import annotations

import json

from langchain.messages import HumanMessage

from .state import ValidationInput, ValidationOutput


def build_validation_messages(validation_input: ValidationInput) -> list[HumanMessage]:
    """Build validator messages for structured output."""
    prompt = json.dumps(
        validation_input.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
    )
    return [HumanMessage(content=prompt)]


def deterministic_validation_failure(validation_input: ValidationInput) -> ValidationOutput | None:
    """Reject SQL outputs that are invalid without needing model judgment."""
    sql_result = validation_input.sql_result
    if sql_result is None:
        return ValidationOutput(
            valid=False,
            retryable=True,
            summary="SQL execution did not return a result payload.",
            instructions=["Produce a non-empty SQL result before validation."],
        )

    if sql_result.get("status") != "ok":
        summary = str(sql_result.get("message") or sql_result.get("summary") or "SQL execution failed.")
        return ValidationOutput(
            valid=False,
            retryable=True,
            summary=summary,
            instructions=[summary],
        )

    row_count = int(sql_result.get("row_count", 0) or 0)
    if row_count <= 0:
        return ValidationOutput(
            valid=False,
            retryable=True,
            summary="SQL returned no rows.",
            instructions=["Adjust the query so it returns rows that answer the task."],
        )

    return None
