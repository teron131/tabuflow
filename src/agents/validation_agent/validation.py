"""Structured validation worker for workflow SQL results."""

from __future__ import annotations

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig

from ..base import ApplicationAgent
from .nodes import build_validation_messages, deterministic_validation_failure
from .prompts import VALIDATION_SYSTEM_PROMPT
from .state import ValidationInput, ValidationOutput


class ValidationAgent(ApplicationAgent):
    """Run one lightweight validation pass over a SQL result."""

    def __init__(self, *, llm: BaseChatModel | None = None):
        super().__init__(llm=llm)
        self.validator = self.build_structured_agent(
            ValidationOutput,
            system_prompt=VALIDATION_SYSTEM_PROMPT,
            name="validation_agent",
        )

    def validate(
        self,
        validation_input: ValidationInput,
        *,
        config: RunnableConfig | None = None,
    ) -> ValidationOutput:
        """Run deterministic checks and the structured validation model."""
        deterministic_failure = deterministic_validation_failure(validation_input)
        if deterministic_failure is not None:
            return deterministic_failure

        result = self.validator.invoke(
            {"messages": build_validation_messages(validation_input)},
            config=config,
        )
        return self.get_structured_response(
            result,
            ValidationOutput,
            agent_name="validation_agent",
        )

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
        config: RunnableConfig | None = None,
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
        return self.validate(validation_input, config=config)
