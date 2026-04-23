"""LangGraph validation worker for workflow validation."""

from __future__ import annotations

import json
import re
from typing import Any, cast

from langchain.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.language_models import BaseChatModel
from langchain_core.utils.json import parse_json_markdown
from langgraph.graph import START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ...clients.openai import ChatOpenAI
from ..config import DEFAULT_AGENT_MODEL, DEFAULT_REASONING_EFFORT, get_agent_settings
from .state import ValidationInput, ValidationOutput, ValidationState

VALIDATION_SYSTEM_PROMPT = """Review whether a SQL result appears to satisfy the user's task.

Rules:
- Focus on task fulfillment, not stylistic SQL preferences.
- Use the task, prepared targets, selected targets, SQL text, and SQL result payload.
- If the result looks incomplete, wrong-grain, empty, or suspicious, set valid=false.
- If another SQL attempt could plausibly fix it, set retryable=true and provide short concrete instructions.
- Keep the feedback concise and directly actionable.
"""


def _coerce_validation_output_from_raw(
    raw_text: str,
    parsing_error: BaseException | None,
) -> ValidationOutput:
    """Recover one validation output from raw model text when structured parsing fails."""
    try:
        return ValidationOutput.model_validate(parse_json_markdown(raw_text))
    except Exception:
        summary = raw_text or (str(parsing_error) if parsing_error is not None else "Validation model did not return a usable structured result.")
        normalized_summary = summary.lower()
        valid_match = re.search(r"\bvalid\b\s*[:=]\s*(true|false)", normalized_summary)
        retryable_match = re.search(r"\bretryable\b\s*[:=]\s*(true|false)", normalized_summary)

        valid = valid_match.group(1) == "true" if valid_match else False
        if retryable_match:
            retryable = retryable_match.group(1) == "true"
        elif valid:
            retryable = False
        else:
            retryable = True

        return ValidationOutput(
            valid=valid,
            retryable=retryable,
            summary=summary,
            instructions=[summary] if summary and retryable else [],
        )


class ValidationAgent:
    """Run one lightweight validation pass over a SQL result."""

    def __init__(self, *, llm: BaseChatModel | None = None):
        if llm is None:
            settings = get_agent_settings()
            resolved_model = settings.fast_llm or settings.quality_llm or DEFAULT_AGENT_MODEL
            llm = ChatOpenAI(
                model=resolved_model,
                temperature=0,
                reasoning_effort=DEFAULT_REASONING_EFFORT,
            )
        self.llm = llm
        self.validator = self.llm.with_structured_output(
            ValidationOutput,
            method="function_calling",
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
        prompt = json.dumps(
            ValidationInput.model_validate(state).model_dump(mode="json"),
            ensure_ascii=True,
            sort_keys=True,
        )
        messages = [
            SystemMessage(content=VALIDATION_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ]
        try:
            output = cast(ValidationOutput, self.validator.invoke(messages))
        except Exception as exc:
            raw_message = cast(AIMessage, self.llm.invoke(messages))
            output = _coerce_validation_output_from_raw(str(raw_message.text).strip(), exc)
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
