"""LangGraph validation worker for workflow validation."""

from __future__ import annotations

import json
from typing import Any

from langchain.messages import HumanMessage
from langchain_core.language_models import BaseChatModel
from langgraph.graph import START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..base import ApplicationAgent
from .state import ValidationInput, ValidationOutput, ValidationState

VALIDATION_SYSTEM_PROMPT = """Review whether a SQL result appears to satisfy the user's task.

Rules:
- Focus on task fulfillment, not stylistic SQL preferences.
- Use the task, prepared targets, selected targets, SQL text, and SQL result payload.
- Prefer accepting useful, non-empty results that answer the main request even if they are not exhaustive.
- For summary tasks, set valid=true when the result includes the requested main totals plus at least one meaningful breakdown or notable pattern.
- Do not reject a result only because one breakdown label is blank, formatting is imperfect, or additional nice-to-have context could be added.
- Set valid=false only when the result is empty, wrong-grain, unrelated to the task, missing the requested main metric, or obviously suspicious.
- If another SQL attempt could plausibly fix it, set retryable=true and provide short concrete instructions.
- Keep the feedback concise and directly actionable.
"""


def _build_validation_messages(validation_input: ValidationInput) -> list[HumanMessage]:
    """Build validator messages for structured output."""
    prompt = json.dumps(
        validation_input.model_dump(mode="json"),
        ensure_ascii=True,
        sort_keys=True,
    )
    return [HumanMessage(content=prompt)]


def _deterministic_validation_failure(validation_input: ValidationInput) -> ValidationOutput | None:
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


class ValidationAgent(ApplicationAgent):
    """Run one lightweight validation pass over a SQL result."""

    def __init__(self, *, llm: BaseChatModel | None = None):
        super().__init__(llm=llm)
        self.validator = self.build_structured_agent(
            ValidationOutput,
            system_prompt=VALIDATION_SYSTEM_PROMPT,
            name="validation_agent_decision",
        )
        self.graph = self.build_graph()

    def build_graph(self) -> CompiledStateGraph:
        """Build the compiled validation graph."""
        builder = StateGraph(
            ValidationState,
            input_schema=ValidationInput,
            output_schema=ValidationOutput,
        )
        builder.add_node("validate", self.validate_node)
        builder.add_edge(START, "validate")
        return builder.compile(name="validation_agent")

    def validate_node(self, state: ValidationState) -> dict[str, Any]:
        """Run the structured validation model for one graph invocation."""
        validation_input = ValidationInput.model_validate(state)
        deterministic_failure = _deterministic_validation_failure(validation_input)
        if deterministic_failure is not None:
            return deterministic_failure.model_dump(mode="json")

        result = self.validator.invoke({"messages": _build_validation_messages(validation_input)})
        output: ValidationOutput = self.get_structured_response(
            result,
            ValidationOutput,
            agent_name="validation_agent_decision",
        )
        return output.model_dump(mode="json")

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
        validation_output = self.graph.invoke(validation_input)
        return ValidationOutput.model_validate(validation_output)
