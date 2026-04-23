"""Simple structured-output agent for workflow validation."""

from __future__ import annotations

import json
import os
from typing import Any

from langchain_core.language_models import BaseChatModel

from ...clients.openai import ChatOpenAI
from .state import ValidationInput, ValidationOutput

DEFAULT_VALIDATION_MODEL = "openai/gpt-5.4-nano"

VALIDATION_SYSTEM_PROMPT = """Review whether a SQL result appears to satisfy the user's task.

Rules:
- Focus on task fulfillment, not stylistic SQL preferences.
- Use the task, prepared targets, selected targets, SQL text, and SQL result payload.
- If the result looks incomplete, wrong-grain, empty, or suspicious, set valid=false.
- If another SQL attempt could plausibly fix it, set retryable=true and provide short concrete instructions.
- Keep the feedback concise and directly actionable.
"""


class ValidationAgent:
    """Run one lightweight validation pass over a SQL result."""

    def __init__(self, *, llm: BaseChatModel | None = None):
        if llm is None:
            resolved_model = os.getenv("FAST_LLM") or os.getenv("QUALITY_LLM") or DEFAULT_VALIDATION_MODEL
            llm = ChatOpenAI(
                model=resolved_model,
                temperature=0,
                reasoning_effort="high",
            )
        self.llm = llm
        self.validator = self.build_validator()

    def build_validator(self) -> Any:
        """Build the structured-output validator."""
        return self.llm.with_structured_output(ValidationOutput)

    def invoke(
        self,
        *,
        task: str,
        source_files: list[str],
        extracted_targets: list[dict[str, Any]],
        selected_targets: list[str],
        candidate_sql: str | None,
        sql_result: dict[str, Any] | None,
        previous_feedback: dict[str, Any] | None,
        validation_attempts: int,
    ) -> ValidationOutput:
        """Validate one SQL result against the original task."""
        validation_input = ValidationInput(
            task=task,
            source_files=source_files,
            extracted_targets=extracted_targets,
            selected_targets=selected_targets,
            candidate_sql=candidate_sql,
            sql_result=sql_result,
            previous_feedback=previous_feedback,
            validation_attempts=validation_attempts,
        )
        prompt = (
            VALIDATION_SYSTEM_PROMPT
            + "\n\n"
            + json.dumps(
                validation_input.model_dump(mode="json"),
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return self.validator.invoke(prompt)
