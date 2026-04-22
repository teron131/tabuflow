"""Workflow nodes for the deterministic tabular analysis agent."""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Literal

from langchain.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from ..sql_agent import SQLAgent
from ...tools import search_skills
from ...tools.sql.query import describe_target, save_view

from .payloads import build_answer_payload, compact_sql_agent_output, compact_validation_feedback
from .prompts import FINAL_ANSWER_SYSTEM_PROMPT, build_task_prompt
from .state import TabularTaskState, append_trace

DEFAULT_VIEW_NAME = "analysis_result"
VIEW_NAME_STOP_WORDS = {"a", "an", "and", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
SKILLS_PATH = "skills"
StateUpdate = dict[str, Any]
MAX_VALIDATION_TARGETS = 2
MAX_VALIDATION_SAMPLE_ROWS = 2
MAX_VALIDATION_TEXT_HINTS = 2
MAX_VALIDATION_ATTEMPTS = 2

VALIDATE_RESULT_SYSTEM_PROMPT = """Review the SQL attempt for task fulfillment.

Rules:
- Focus on whether the SQL result appears to answer the user's task with the available tables and fields.
- Use the task, selected target schemas, candidate SQL, and result preview.
- If the result looks incomplete, wrong-grain, or suspicious, set valid=false.
- If required fields appear missing from the chosen targets, set valid=false.
- Keep feedback short and actionable for the next SQL attempt.
- Do not ask for human clarification unless the task is fundamentally ambiguous.
"""


class ValidationDecision(BaseModel):
    """Structured validation output for the tabular SQL loop."""

    valid: bool = Field(description="Whether the SQL attempt appears to fulfill the task.")
    retryable: bool = Field(default=True, description="Whether another SQL attempt is likely to help.")
    failure_type: Literal["wrong_target", "wrong_grain", "missing_metric", "missing_filter", "empty_result", "suspicious_result", "ambiguous_task", "other"] = Field(
        default="other",
        description="Best matching reason when valid is false.",
    )
    summary: str = Field(default="", description="Short summary of what is wrong with the result.")
    instructions: list[str] = Field(default_factory=list, description="Concrete instructions for the next SQL attempt.")
    rationale: str = Field(default="", description="Brief explanation of the validation decision.")


def suggest_view_name(task: str) -> str:
    """Build a deterministic snake_case view name from the task."""
    tokens = [token for token in re.findall(r"[a-z0-9]+", task.lower()) if token not in VIEW_NAME_STOP_WORDS]
    base = "_".join(tokens[:6]) or DEFAULT_VIEW_NAME
    if base[0].isdigit():
        base = f"analysis_{base}"
    return base


def collect_targets(extraction_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return compact extracted target metadata for the final answer payload."""
    targets: list[dict[str, Any]] = []
    for extraction in extraction_results:
        for table in extraction.get("tables", []):
            targets.append(
                {
                    "source_path": extraction.get("path"),
                    "table_name": table.get("table_name"),
                    "typed_view_name": table.get("typed_view_name"),
                    "row_count": table.get("row_count"),
                }
            )
    return targets


def _validation_target_context(state: TabularTaskState) -> list[dict[str, Any]]:
    """Return compact schema context for the validator."""
    if not state.database_path:
        return []

    contexts = []
    for target_name in state.selected_targets[:MAX_VALIDATION_TARGETS]:
        description = describe_target(
            target_name,
            database_path=state.database_path,
            sample_rows=MAX_VALIDATION_SAMPLE_ROWS,
            text_value_hints=MAX_VALIDATION_TEXT_HINTS,
        )
        if description.get("status") != "ok":
            continue

        contexts.append(
            {
                "name": description.get("name"),
                "kind": description.get("kind"),
                "summary": description.get("summary"),
                "columns": [
                    {
                        "name": column.get("name"),
                        "type": column.get("type"),
                    }
                    for column in description.get("columns", [])
                ],
                "sample_rows": description.get("sample_rows", []),
                "text_value_hints": description.get("text_value_hints", {}),
            }
        )
    return contexts


def traced_update(
    state: TabularTaskState,
    trace_message: str,
    **values: Any,
) -> StateUpdate:
    """Return one state update with a trace entry appended."""
    return {
        **values,
        "trace": append_trace(state, trace_message),
    }


def error_update(
    state: TabularTaskState,
    *,
    last_error: str,
    trace_message: str,
    **values: Any,
) -> StateUpdate:
    """Return one error state update with the shared trace shape."""
    return traced_update(
        state,
        trace_message,
        status="error",
        last_error=last_error,
        **values,
    )


def _serialize_validation_feedback(decision: ValidationDecision) -> dict[str, Any]:
    """Return the structured retry payload stored in graph state."""
    instructions = [instruction.strip() for instruction in decision.instructions if instruction.strip()]
    summary = decision.summary.strip()
    if not instructions and summary:
        instructions = [summary]
    if not summary:
        summary = "The SQL result does not appear to fully satisfy the task."
    return {
        "failure_type": decision.failure_type,
        "retryable": decision.retryable,
        "summary": summary,
        "instructions": instructions,
        "rationale": decision.rationale.strip(),
    }


def make_skills_node():
    """Create the skills-search node for task-time graph context."""

    def skills_node(state: TabularTaskState) -> StateUpdate:
        skills_result = search_skills.invoke(
            {
                "path": SKILLS_PATH,
                "query": state.task,
            }
        )
        skills = skills_result.get("skills", [])
        skill_lines = [f"- {skill['name']}: {skill['description']} ({skill['path']}, score={skill['score']})" for skill in skills]
        section_lines = (
            [
                f"Relevant workspace skills discovered by `search_skills` under `{SKILLS_PATH}` for this task:",
                *skill_lines,
            ]
            if skill_lines
            else [f"Relevant workspace skills discovered by `search_skills` under `{SKILLS_PATH}` for this task: none above threshold."]
        )
        if diagnostics := skills_result.get("diagnostics", []):
            section_lines.extend(["Skill discovery diagnostics:", *(f"- {message}" for message in diagnostics)])

        matched_skill_names = [str(skill["name"]) for skill in skills]
        return traced_update(
            state,
            f"matched {len(matched_skill_names)} workspace skills",
            matched_skill_names=matched_skill_names,
            search_context="\n".join(section_lines),
        )

    return skills_node


def make_prep_node(tabular_tools: list[Any]):
    """Create the preparation node from the tabular tool surface."""
    extract_tabular = next((tool for tool in tabular_tools if getattr(tool, "name", None) == "extract_tabular"), None)
    if extract_tabular is None:
        raise ValueError("Missing tool: extract_tabular")

    def prep_node(state: TabularTaskState) -> StateUpdate:
        extraction_results: list[dict[str, Any]] = []
        database_paths: set[str] = set()
        for source_file in state.source_files:
            extraction = extract_tabular.invoke({"path": source_file})
            extraction_results.append(extraction)
            if extraction.get("status") != "loaded":
                return error_update(
                    state,
                    extraction_results=extraction_results,
                    last_error=extraction.get("message", f"Extraction failed for {source_file}"),
                    outcome="failed",
                    completion_reason="prep_failed",
                    trace_message=f"prep failed for {source_file}",
                )
            if database_path := extraction.get("database_path"):
                database_paths.add(str(database_path))

        if len(database_paths) != 1:
            return error_update(
                state,
                extraction_results=extraction_results,
                last_error="Expected one shared SQLite database path after extraction.",
                outcome="failed",
                completion_reason="prep_inconsistent_database_paths",
                trace_message="prep produced inconsistent database paths",
            )

        database_path = next(iter(database_paths))
        targets = collect_targets(extraction_results)
        return traced_update(
            state,
            f"prepared {len(targets)} targets into {database_path}",
            status="prepared",
            database_path=database_path,
            extraction_results=extraction_results,
            extracted_targets=targets,
        )

    return prep_node


def make_sql_node(
    *,
    llm: Any,
    prompt: str,
):
    """Create the SQL-agent node."""
    sql_agent = SQLAgent(llm=llm)

    def sql_node(state: TabularTaskState) -> StateUpdate:
        if not state.database_path:
            return error_update(
                state,
                last_error="No SQLite database path was available for SQL analysis.",
                outcome="failed",
                completion_reason="missing_database_path",
                trace_message="sql skipped because database_path was missing",
            )

        view_name = suggest_view_name(state.task)
        with sqlite3.connect(state.database_path) as connection:
            connection.execute(f'DROP VIEW IF EXISTS "{view_name}"')
            connection.commit()

        task_prompt = build_task_prompt(
            prompt,
            state.task,
            state.source_files,
            search_context=state.search_context,
        )
        if state.validation_feedback:
            task_prompt = "\n\n".join(
                [
                    task_prompt,
                    "Validation retry guidance for the next SQL attempt:",
                    json.dumps(compact_validation_feedback(state.validation_feedback), ensure_ascii=True, sort_keys=True),
                ]
            )
        sql_output = sql_agent.invoke(
            task_prompt,
            database_path=state.database_path,
        )
        trace_message = f"sql agent finished with status={sql_output.status}"
        if sql_output.selected_targets:
            targets = ", ".join(sql_output.selected_targets)
            trace_message += f" on {targets}"
        else:
            trace_message += f" after clearing stale view {view_name}"
        return traced_update(
            state,
            trace_message,
            status=sql_output.status,
            outcome="pending" if sql_output.status == "complete" else ("blocked" if sql_output.status == "blocked" else "failed"),
            completion_reason=None if sql_output.status == "complete" else ("sql_blocked" if sql_output.status == "blocked" else "sql_execution_failed"),
            sql_agent_output=compact_sql_agent_output(sql_output.model_dump(mode="json")),
            selected_targets=sql_output.selected_targets,
            candidate_sql=sql_output.candidate_sql,
            sql_result=sql_output.result,
            last_error=None if sql_output.status == "complete" else sql_output.last_error,
        )

    return sql_node


def make_validate_node(llm: Any):
    """Create the post-SQL validation node."""
    validator = llm.with_structured_output(ValidationDecision)

    def validate_node(state: TabularTaskState) -> StateUpdate:
        if state.status != "complete" or not state.candidate_sql or state.sql_result is None:
            return traced_update(
                state,
                "validation skipped because no completed SQL result was available",
                status=state.status,
            )

        target_context = _validation_target_context(state)
        validation_payload = {
            "task": state.task,
            "selected_targets": state.selected_targets,
            "candidate_sql": state.candidate_sql,
            "sql_result": state.sql_result,
            "target_context": target_context,
            "previous_feedback": state.validation_feedback,
            "validation_attempts": state.validation_attempts,
        }
        decision: ValidationDecision = validator.invoke(
            [
                SystemMessage(content=VALIDATE_RESULT_SYSTEM_PROMPT),
                HumanMessage(content=json.dumps(validation_payload, ensure_ascii=True, sort_keys=True)),
            ]
        )
        if decision.valid:
            return traced_update(
                state,
                "validation accepted the SQL result",
                status="validated",
                outcome="fulfilled",
                completion_reason="validated_result",
                validation_feedback=None,
                last_error=None,
            )

        feedback = _serialize_validation_feedback(decision)
        summary = str(feedback["summary"])
        status = "needs_revision" if decision.retryable else "blocked"
        next_attempts = state.validation_attempts + (1 if decision.retryable else 0)
        exhausted_retries = decision.retryable and next_attempts >= MAX_VALIDATION_ATTEMPTS
        return traced_update(
            state,
            "validation requested another SQL attempt" if decision.retryable else "validation blocked the run",
            status=status,
            outcome="failed" if exhausted_retries else ("blocked" if not decision.retryable else "pending"),
            completion_reason="validation_attempt_limit" if exhausted_retries else ("validation_blocked" if not decision.retryable else None),
            validation_attempts=next_attempts,
            validation_feedback=feedback,
            last_error=summary,
        )

    return validate_node


def save_node(state: TabularTaskState) -> StateUpdate:
    """Save the final SQL query result as a reusable SQLite view."""
    if not state.candidate_sql or not state.database_path:
        return traced_update(
            state,
            status="error",
            last_error="No executable SQL was available to save as a view.",
            outcome=state.outcome,
            completion_reason="save_failed",
            trace_message="save skipped because candidate_sql was missing",
        )

    view_name = suggest_view_name(state.task)
    saved_view = save_view(
        state.candidate_sql,
        view_name,
        database_path=state.database_path,
        replace=True,
    )
    if saved_view.get("status") != "ok":
        return traced_update(
            state,
            status="error",
            saved_view_name=view_name,
            saved_view=saved_view,
            last_error=saved_view.get("message", f"Failed to save view {view_name}"),
            outcome=state.outcome,
            completion_reason="save_failed",
            trace_message=f"save failed for view {view_name}",
        )

    return traced_update(
        state,
        f"saved result as view {view_name}",
        status="saved",
        outcome=state.outcome,
        completion_reason="saved_view",
        saved_view_name=view_name,
        saved_view=saved_view,
        last_error=None,
    )


def make_answer_node(llm: Any, *, prompt: str):
    """Create the final answer composition node."""

    def answer_node(state: TabularTaskState) -> StateUpdate:
        task_prompt = build_task_prompt(
            prompt,
            state.task,
            state.source_files,
            search_context=state.search_context,
        )
        execution_payload = build_answer_payload(
            task=state.task,
            status=state.status,
            outcome=state.outcome,
            completion_reason=state.completion_reason,
            source_files=state.source_files,
            database_path=state.database_path,
            extracted_targets=state.extracted_targets,
            selected_targets=state.selected_targets,
            candidate_sql=state.candidate_sql,
            sql_result=state.sql_result,
            saved_view_name=state.saved_view_name,
            last_error=state.last_error,
            validation_feedback=state.validation_feedback,
        )
        response = llm.invoke(
            [
                SystemMessage(content=FINAL_ANSWER_SYSTEM_PROMPT),
                HumanMessage(content=(f"{task_prompt}\n\nExecution result:\n{json.dumps(execution_payload, ensure_ascii=True, sort_keys=True)}")),
            ]
        )
        return traced_update(
            state,
            "generated final answer",
            final_answer=getattr(response, "content", str(response)),
        )

    return answer_node
