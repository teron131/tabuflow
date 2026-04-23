"""Tool wrappers exposed to the top-level orchestrator graph."""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from langchain.tools import BaseTool, tool
from pydantic import BaseModel, Field

from ...tools import list_skills, load_skills, search_skills
from ...tools.sql.query import save_view
from ..prep_agent import PrepAgent
from ..sql_agent import SQLAgent
from .payloads import build_result_artifact, build_result_message

SKILLS_PATH = "skills"
DEFAULT_VIEW_NAME = "analysis_result"
VIEW_NAME_STOP_WORDS = {"a", "an", "and", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
MAX_SKILL_REF_PREVIEW = 8


class ValidationDecision(BaseModel):
    """Simple orchestrator-owned validation result for SQL output."""

    valid: bool = Field(description="Whether the SQL result appears to satisfy the task.")
    retryable: bool = Field(default=True, description="Whether another SQL attempt is likely to help.")
    summary: str = Field(default="", description="Short explanation of the validation judgment.")
    instructions: list[str] = Field(default_factory=list, description="Concrete guidance for the next SQL attempt when retryable.")


VALIDATION_SYSTEM_PROMPT = """Review whether a SQL result appears to satisfy the user's task.

Rules:
- Focus on task fulfillment, not stylistic SQL preferences.
- Use the task, prepared targets, selected targets, SQL text, and SQL result payload.
- If the result looks incomplete, wrong-grain, empty, or suspicious, set valid=false.
- If another SQL attempt could plausibly fix it, set retryable=true and provide short concrete instructions.
- Keep the feedback concise and directly actionable.
"""


def list_skills_context(*, path: str = SKILLS_PATH) -> dict[str, Any]:
    """Return the raw workspace-skills listing payload."""
    return list_skills.invoke({"path": path})


def search_skills_context(
    query: str,
    *,
    path: str = SKILLS_PATH,
) -> dict[str, Any]:
    """Return the raw semantic workspace-skills search payload."""
    return search_skills.invoke(
        {
            "path": path,
            "query": query,
        }
    )


def format_skills_overview(result: dict[str, Any]) -> str:
    """Render a deterministic system-prompt section for available skills."""
    diagnostics = [str(item) for item in result.get("diagnostics", [])]
    if result.get("status") == "error":
        message = "; ".join(diagnostics) or "unknown error"
        return f"Workspace skills listing failed: {message}"

    skills = list(result.get("skills", []))
    lines = ["Workspace skills available under `skills`:"]
    if skills:
        for skill in skills:
            lines.append(f"- {skill.get('name', 'unknown')}: {str(skill.get('description', '')).strip()} ({skill.get('path', '')})")
    else:
        lines.append("- none found")

    if diagnostics:
        lines.append(f"Diagnostics: {'; '.join(diagnostics[:3])}")
    return "\n".join(lines)


def format_skill_matches(result: dict[str, Any]) -> str:
    """Render a deterministic user-turn section for relevant skills."""
    diagnostics = [str(item) for item in result.get("diagnostics", [])]
    if result.get("status") == "error":
        message = "; ".join(diagnostics) or "unknown error"
        return f"Workspace skills search failed: {message}"

    skills = list(result.get("skills", []))
    lines = []
    if skills:
        for skill in skills:
            score = skill.get("score")
            suffix = f", score={score}" if score is not None else ""
            lines.append(f"- {skill.get('name', 'unknown')}: {str(skill.get('description', '')).strip()} ({skill.get('path', '')}{suffix})")
    else:
        lines.append("- none above threshold")

    if diagnostics:
        lines.append(f"Diagnostics: {'; '.join(diagnostics[:3])}")
    return "\n".join(lines)


def _summarize_sql_output(output: dict[str, Any]) -> str:
    """Render a concise summary of the SQL worker output."""
    status = str(output.get("status", "pending"))
    selected_targets = [str(item) for item in output.get("selected_targets", [])]
    result = output.get("result") or {}

    if status == "complete":
        lines = ["SQL workflow completed."]
        if selected_targets:
            lines.append(f"Targets: {', '.join(selected_targets[:4])}")
        row_count = result.get("row_count")
        if row_count is not None:
            lines.append(f"Rows: {row_count}")
        if summary := result.get("summary"):
            lines.append(f"Summary: {summary}")
        return "\n".join(lines)

    last_error = output.get("last_error")
    if last_error:
        return f"SQL workflow ended with status={status}: {last_error}"
    return f"SQL workflow ended with status={status}."


def _build_worker_skill_payload(
    task: str,
    *,
    path: str = SKILLS_PATH,
) -> dict[str, Any]:
    """Search and load matched skills into one worker-ready payload."""
    search_result = search_skills_context(task, path=path)
    matched_skills = list(search_result.get("skills", []))
    worker_sections: list[str] = []
    skill_refs: list[dict[str, Any]] = []
    diagnostics = [str(item) for item in search_result.get("diagnostics", [])]

    for skill in matched_skills:
        skill_name = str(skill.get("name", "")).strip()
        if not skill_name:
            continue

        load_result = load_skills.invoke(
            {
                "path": path,
                "skills": skill_name,
            }
        )
        diagnostics.extend(str(item) for item in load_result.get("diagnostics", []))
        loaded_skills = list(load_result.get("skills", []))
        if not loaded_skills:
            continue
        loaded_skill = loaded_skills[0]

        description = str(loaded_skill.get("description", "")).strip()
        instructions_payload = loaded_skill.get("instructions") or {}
        instructions = str(instructions_payload.get("content", "")).strip()
        if instructions:
            worker_sections.append(f"Skill `{loaded_skill.get('name', 'unknown')}`: {description}".strip())
            worker_sections.append(instructions)
        skill_refs.append(loaded_skill)

    if diagnostics:
        worker_sections.append("Skill loading diagnostics:")
        worker_sections.extend(f"- {message}" for message in diagnostics)

    return {
        "worker_instructions": "\n\n".join(section for section in worker_sections if section.strip()),
        "skill_refs": skill_refs,
    }


def _format_source_file_list(source_files: list[str]) -> str:
    """Render the source file list once for prompt construction."""
    return "\n".join(f"- {source_file}" for source_file in source_files) or "- (none)"


def _summarize_skill_refs(skill_refs: list[dict[str, Any]]) -> str:
    """Render a compact summary of worker-visible skill references."""
    if not skill_refs:
        return ""

    lines = ["Skill refs available to this run:"]
    for skill_ref in skill_refs[:MAX_SKILL_REF_PREVIEW]:
        skill_name = str(skill_ref.get("name", "unknown"))
        instructions = skill_ref.get("instructions") or {}
        instruction_path = str(instructions.get("relative_path") or skill_ref.get("path") or "")
        reference_count = len(skill_ref.get("references", [])) if isinstance(skill_ref.get("references"), list) else 0
        script_count = len(skill_ref.get("scripts", [])) if isinstance(skill_ref.get("scripts"), list) else 0
        summary_parts = [instruction_path] if instruction_path else []
        if reference_count:
            summary_parts.append(f"{reference_count} refs")
        if script_count:
            summary_parts.append(f"{script_count} scripts")
        lines.append(f"- {skill_name}: {', '.join(summary_parts) if summary_parts else 'loaded'}")
    if len(skill_refs) > MAX_SKILL_REF_PREVIEW:
        lines.append(f"- ... (+{len(skill_refs) - MAX_SKILL_REF_PREVIEW} more)")
    return "\n".join(lines)


def _build_sql_task_prompt(
    prompt: str,
    task: str,
    source_files: list[str],
    *,
    worker_instructions: str,
    skill_refs: list[dict[str, Any]],
    validation_feedback: dict[str, Any] | None = None,
) -> str:
    """Build the SQL worker prompt for one orchestrator-owned run."""
    parts = [prompt.strip()] if prompt.strip() else []
    parts.append(f"Source files:\n{_format_source_file_list(source_files)}\nTask: {task}")
    if worker_instructions.strip():
        parts.append(worker_instructions.strip())
    if skill_ref_summary := _summarize_skill_refs(skill_refs):
        parts.append(skill_ref_summary)
    if validation_feedback:
        parts.append(
            "\n".join(
                [
                    "Validation retry guidance for the next SQL attempt:",
                    json.dumps(validation_feedback, ensure_ascii=True, sort_keys=True),
                ]
            )
        )
    return "\n\n".join(parts)


def _build_saved_view_name(task: str, run_id: str) -> str:
    """Build a deterministic SQLite view name from the task and run id."""
    tokens = [token for token in re.findall(r"[a-z0-9]+", task.lower()) if token not in VIEW_NAME_STOP_WORDS]
    base = "_".join(tokens[:6]) or DEFAULT_VIEW_NAME
    if base[0].isdigit():
        base = f"analysis_{base}"
    safe_run_id = "_".join(re.findall(r"[a-z0-9]+", run_id.lower())) or "manual"
    return f"{base}__{safe_run_id[:12]}"


def _append_trace(trace: list[str], message: str) -> list[str]:
    """Append one trace message while keeping the trace compact."""
    return [*trace, message][-12:]


def _validate_result(
    *,
    llm: Any,
    task: str,
    source_files: list[str],
    extracted_targets: list[dict[str, Any]],
    selected_targets: list[str],
    candidate_sql: str | None,
    sql_result: dict[str, Any] | None,
    previous_feedback: dict[str, Any] | None,
    validation_attempts: int,
) -> ValidationDecision:
    """Run one lightweight orchestrator-owned validation prompt."""
    validator = llm.with_structured_output(ValidationDecision)
    payload = {
        "task": task,
        "source_files": source_files,
        "extracted_targets": extracted_targets,
        "selected_targets": selected_targets,
        "candidate_sql": candidate_sql,
        "sql_result": sql_result,
        "previous_feedback": previous_feedback,
        "validation_attempts": validation_attempts,
    }
    return validator.invoke(VALIDATION_SYSTEM_PROMPT + "\n\n" + json.dumps(payload, ensure_ascii=True, sort_keys=True))


def make_orchestrator_tools(
    *,
    prompt: str = "",
    root_dir: str | Path | None = None,
    llm: Any | None = None,
) -> list[BaseTool]:
    """Build the static tool set exposed to the top-level orchestrator."""
    prep_agent: PrepAgent | None = None
    sql_agent: SQLAgent | None = None

    def get_prep_agent() -> PrepAgent:
        """Return one cached prep worker instance."""
        nonlocal prep_agent
        if prep_agent is None:
            prep_agent = PrepAgent(
                llm=llm,
                prompt=prompt,
                root_dir=root_dir,
            )
        return prep_agent

    def get_sql_agent() -> SQLAgent:
        """Return one cached SQL worker instance."""
        nonlocal sql_agent
        if sql_agent is None:
            sql_agent = SQLAgent(llm=llm) if llm is not None else SQLAgent()
        return sql_agent

    @tool(parse_docstring=True, response_format="content_and_artifact")
    def run_sql_workflow(
        question: str,
        database_path: str,
        max_suggestions: int = 3,
        max_repairs: int = 2,
    ) -> tuple[str, dict[str, Any]]:
        """Run the SQL worker workflow against an existing SQLite database.

        Args:
            question: Natural-language analysis question to answer with SQL.
            database_path: Absolute or relative SQLite database path.
            max_suggestions: Maximum number of candidate SQL targets to inspect.
            max_repairs: Maximum number of SQL repair attempts.
        """
        sql_agent = get_sql_agent()
        output = sql_agent.invoke(
            question,
            database_path=database_path,
            max_suggestions=max_suggestions,
            max_repairs=max_repairs,
        )
        artifact = output.model_dump(mode="json")
        return _summarize_sql_output(artifact), artifact

    @tool(parse_docstring=True, response_format="content_and_artifact")
    def run_workflow(
        task: str,
        source_files: list[str],
        max_prep_trials: int = 2,
        max_validation_retries: int = 2,
    ) -> tuple[str, dict[str, Any]]:
        """Run the workflow on local files and return its final result.

        Args:
            task: Natural-language task to execute against the supplied files.
            source_files: One or more local spreadsheet or table-like file paths.
            max_prep_trials: Maximum number of prep-agent retries before stopping.
            max_validation_retries: Maximum number of validator-requested SQL retries.
        """
        if llm is None:
            raise ValueError("run_workflow requires an orchestrator LLM for validation.")

        prep_agent = get_prep_agent()
        sql_agent = get_sql_agent()
        skill_payload = _build_worker_skill_payload(task)
        run_id = uuid4().hex[:8]
        trace: list[str] = []

        prep_output = prep_agent.invoke(
            task,
            source_files=source_files,
            worker_instructions=skill_payload["worker_instructions"],
            skill_refs=skill_payload["skill_refs"],
            max_prep_trials=max_prep_trials,
        )

        for message in prep_output.trace:
            trace = _append_trace(trace, message)

        if prep_output.status != "prepared":
            artifact = build_result_artifact(
                task=task,
                status="error",
                outcome="failed",
                completion_reason="prep_failed",
                source_files=source_files,
                database_path=prep_output.database_path,
                extracted_targets=prep_output.extracted_targets,
                selected_targets=[],
                candidate_sql=None,
                sql_result=None,
                saved_view_name=None,
                saved_view=None,
                last_error=prep_output.last_error or "Preparation failed.",
                validation_feedback=None,
                validation_attempts=0,
                trace=trace,
            )
            return build_result_message(artifact), artifact

        validation_feedback: dict[str, Any] | None = None
        validation_attempts = 0
        last_sql_output = None
        sql_output = None

        for attempt_idx in range(max(1, max_validation_retries) + 1):
            sql_prompt = _build_sql_task_prompt(
                prompt,
                task,
                source_files,
                worker_instructions=skill_payload["worker_instructions"],
                skill_refs=skill_payload["skill_refs"],
                validation_feedback=validation_feedback,
            )
            sql_output = sql_agent.invoke(
                sql_prompt,
                database_path=prep_output.database_path,
            )
            last_sql_output = sql_output
            for message in sql_output.trace:
                trace = _append_trace(trace, message)

            if sql_output.status != "complete":
                break

            decision = _validate_result(
                llm=llm,
                task=task,
                source_files=source_files,
                extracted_targets=prep_output.extracted_targets,
                selected_targets=sql_output.selected_targets,
                candidate_sql=sql_output.candidate_sql,
                sql_result=sql_output.result,
                previous_feedback=validation_feedback,
                validation_attempts=validation_attempts,
            )
            if decision.valid:
                trace = _append_trace(trace, "orchestrator validation accepted the SQL result")
                view_name = _build_saved_view_name(task, run_id)
                saved_view = save_view(
                    sql_output.candidate_sql,
                    view_name,
                    database_path=prep_output.database_path,
                    replace=False,
                )
                if saved_view.get("status") != "ok":
                    artifact = build_result_artifact(
                        task=task,
                        status="error",
                        outcome="failed",
                        completion_reason="save_failed",
                        source_files=source_files,
                        database_path=prep_output.database_path,
                        extracted_targets=prep_output.extracted_targets,
                        selected_targets=sql_output.selected_targets,
                        candidate_sql=sql_output.candidate_sql,
                        sql_result=sql_output.result,
                        saved_view_name=view_name,
                        saved_view=saved_view,
                        last_error=saved_view.get("message", f"Failed to save view {view_name}"),
                        validation_feedback=validation_feedback,
                        validation_attempts=validation_attempts,
                        trace=_append_trace(trace, f"save failed for view {view_name}"),
                    )
                    return build_result_message(artifact), artifact

                artifact = build_result_artifact(
                    task=task,
                    status="saved",
                    outcome="fulfilled",
                    completion_reason="saved_view",
                    source_files=source_files,
                    database_path=prep_output.database_path,
                    extracted_targets=prep_output.extracted_targets,
                    selected_targets=sql_output.selected_targets,
                    candidate_sql=sql_output.candidate_sql,
                    sql_result=sql_output.result,
                    saved_view_name=view_name,
                    saved_view=saved_view,
                    last_error=None,
                    validation_feedback=None,
                    validation_attempts=validation_attempts,
                    trace=_append_trace(trace, f"saved result as view {view_name}"),
                )
                return build_result_message(artifact), artifact

            validation_feedback = {
                "retryable": decision.retryable,
                "summary": decision.summary.strip() or "The SQL result does not appear to fully satisfy the task.",
                "instructions": [instruction.strip() for instruction in decision.instructions if instruction.strip()],
            }
            if not validation_feedback["instructions"] and validation_feedback["summary"]:
                validation_feedback["instructions"] = [validation_feedback["summary"]]
            trace = _append_trace(trace, f"orchestrator validation requested another SQL attempt: {validation_feedback['summary']}")
            validation_attempts += 1
            if not decision.retryable or attempt_idx >= max_validation_retries:
                break

        if sql_output is None and last_sql_output is None:
            artifact = build_result_artifact(
                task=task,
                status="error",
                outcome="failed",
                completion_reason="sql_execution_failed",
                source_files=source_files,
                database_path=prep_output.database_path,
                extracted_targets=prep_output.extracted_targets,
                selected_targets=[],
                candidate_sql=None,
                sql_result=None,
                saved_view_name=None,
                saved_view=None,
                last_error="SQL workflow did not run.",
                validation_feedback=validation_feedback,
                validation_attempts=validation_attempts,
                trace=_append_trace(trace, "sql workflow did not run"),
            )
            return build_result_message(artifact), artifact

        sql_output = last_sql_output or sql_output
        if sql_output is not None and sql_output.status != "complete":
            completion_reason = "sql_blocked" if sql_output.status == "blocked" else "sql_execution_failed"
            outcome = "blocked" if sql_output.status == "blocked" else "failed"
            artifact = build_result_artifact(
                task=task,
                status=sql_output.status,
                outcome=outcome,
                completion_reason=completion_reason,
                source_files=source_files,
                database_path=prep_output.database_path,
                extracted_targets=prep_output.extracted_targets,
                selected_targets=sql_output.selected_targets,
                candidate_sql=sql_output.candidate_sql,
                sql_result=sql_output.result,
                saved_view_name=None,
                saved_view=None,
                last_error=sql_output.last_error,
                validation_feedback=validation_feedback,
                validation_attempts=validation_attempts,
                trace=trace,
            )
            return build_result_message(artifact), artifact

        completion_reason = "validation_attempt_limit"
        outcome = "failed"
        if validation_feedback and not validation_feedback.get("retryable", True):
            completion_reason = "validation_blocked"
            outcome = "blocked"

        artifact = build_result_artifact(
            task=task,
            status="error",
            outcome=outcome,
            completion_reason=completion_reason,
            source_files=source_files,
            database_path=prep_output.database_path,
            extracted_targets=prep_output.extracted_targets,
            selected_targets=[] if sql_output is None else sql_output.selected_targets,
            candidate_sql=None if sql_output is None else sql_output.candidate_sql,
            sql_result=None if sql_output is None else sql_output.result,
            saved_view_name=None,
            saved_view=None,
            last_error=None if validation_feedback is None else validation_feedback["summary"],
            validation_feedback=validation_feedback,
            validation_attempts=validation_attempts,
            trace=trace,
        )
        return build_result_message(artifact), artifact

    return [
        load_skills,
        run_sql_workflow,
        run_workflow,
    ]
