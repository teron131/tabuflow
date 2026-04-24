"""Direct prep-SQL-validation workflow execution for the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import patch_config
from langsmith import traceable

from ...tools.sql.query import save_view
from ..prep_agent import PrepAgent
from ..sql_agent import SQLAgent, SQLAgentOutput
from ..validation_agent import ValidationAgent, ValidationOutput
from .payloads import build_result_artifact, build_result_message
from .skill_context import WorkerSkillPayload, build_worker_skill_payload, summarize_skill_refs

DEFAULT_VIEW_NAME = "analysis_result"
PREP_AGENT_NAME = "prep_agent"
SQL_AGENT_NAME = "sql_agent"
VALIDATION_AGENT_NAME = "validation_agent"


@dataclass
class WorkflowRun:
    """Shared workflow state used across prep, SQL, and save stages."""

    task: str
    source_files: list[str]
    skill_payload: WorkerSkillPayload
    run_id: str = field(default_factory=lambda: uuid4().hex[:8])
    trace: list[str] = field(default_factory=list)
    database_path: str | None = None
    extracted_targets: list[dict[str, Any]] = field(default_factory=list)

    def append_trace(self, *messages: str) -> None:
        """Append one or more trace messages while keeping the trace compact."""
        for message in messages:
            self.trace = _append_trace(self.trace, message)

    def result(
        self,
        *,
        status: str,
        outcome: str,
        completion_reason: str | None,
        selected_targets: list[str],
        candidate_sql: str | None,
        sql_result: dict[str, Any] | None,
        saved_view_name: str | None,
        saved_view: dict[str, Any] | None,
        last_error: str | None,
        validation_feedback: dict[str, Any] | None,
        validation_attempts: int,
        trace: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build the caller-facing workflow result from the current run state."""
        artifact = build_result_artifact(
            task=self.task,
            status=status,
            outcome=outcome,
            completion_reason=completion_reason,
            source_files=self.source_files,
            database_path=self.database_path,
            extracted_targets=self.extracted_targets,
            selected_targets=selected_targets,
            candidate_sql=candidate_sql,
            sql_result=sql_result,
            saved_view_name=saved_view_name,
            saved_view=saved_view,
            last_error=last_error,
            validation_feedback=validation_feedback,
            validation_attempts=validation_attempts,
            trace=self.trace if trace is None else trace,
        )
        return build_result_message(artifact), artifact


@dataclass
class SqlLoopResult:
    """State accumulated across orchestrator-owned SQL attempts."""

    output: SQLAgentOutput | None = None
    validation_output: ValidationOutput | None = None
    validation_feedback: dict[str, Any] | None = None
    validation_attempts: int = 0
    validated: bool = False


@dataclass
class WorkflowExecutionResult:
    """Normalized result returned by direct and tool-backed workflow execution."""

    content: str
    artifact: dict[str, Any]
    agent_artifacts: dict[str, dict[str, Any]]
    active_agent: str | None


def _append_trace(trace: list[str], message: str) -> list[str]:
    """Append one trace message while keeping the trace compact."""
    return [*trace, message][-12:]


def _preferred_sql_targets(extracted_targets: list[dict[str, Any]]) -> list[str]:
    """Return current-run SQL targets in the order they should be inspected."""
    preferred_targets: list[str] = []
    for target in extracted_targets:
        typed_view_name = str(target.get("typed_view_name", "")).strip()
        table_name = str(target.get("table_name", "")).strip()
        if typed_view_name:
            preferred_targets.append(typed_view_name)
        if table_name:
            preferred_targets.append(table_name)
    return list(dict.fromkeys(preferred_targets))


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
    source_file_list = "\n".join(f"- {source_file}" for source_file in source_files) or "- (none)"
    parts.append(f"Source files:\n{source_file_list}\nTask: {task}")
    if worker_instructions.strip():
        parts.append(worker_instructions.strip())
    if skill_ref_summary := summarize_skill_refs(skill_refs):
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


def _run_sql_validation_loop(
    run: WorkflowRun,
    *,
    prompt: str,
    sql_agent: SQLAgent,
    validation_agent: ValidationAgent,
    max_validation_retries: int,
    config: RunnableConfig | None = None,
) -> SqlLoopResult:
    """Run SQL attempts until validation accepts the result or the retry budget is spent."""
    loop = SqlLoopResult()

    for attempt_idx in range(max(1, max_validation_retries) + 1):
        sql_prompt = _build_sql_task_prompt(
            prompt,
            run.task,
            run.source_files,
            worker_instructions=run.skill_payload.worker_instructions,
            skill_refs=run.skill_payload.skill_refs,
            validation_feedback=loop.validation_feedback,
        )
        sql_output = sql_agent.invoke(
            sql_prompt,
            database_path=run.database_path,
            preferred_targets=_preferred_sql_targets(run.extracted_targets),
            config=patch_config(config, run_name=f"sql_agent_attempt_{attempt_idx + 1}"),
        )
        loop.output = sql_output
        run.append_trace(*sql_output.trace)

        if sql_output.status != "complete":
            return loop

        decision = validation_agent.invoke(
            task=run.task,
            source_files=run.source_files,
            extracted_targets=run.extracted_targets,
            selected_targets=sql_output.selected_targets,
            candidate_sql=sql_output.candidate_sql,
            sql_result=sql_output.result,
            previous_feedback=loop.validation_feedback,
            validation_attempts=loop.validation_attempts,
            config=patch_config(config, run_name=f"validation_agent_attempt_{loop.validation_attempts + 1}"),
        )
        loop.validation_output = decision
        if decision.valid:
            loop.validated = True
            run.append_trace("orchestrator validation accepted the SQL result")
            return loop

        summary = decision.summary.strip() or "The SQL result does not appear to fully satisfy the task."
        instructions = [instruction.strip() for instruction in decision.instructions if instruction.strip()]
        loop.validation_feedback = {
            "retryable": decision.retryable,
            "summary": summary,
            "instructions": instructions or [summary],
        }
        validation_summary = loop.validation_feedback["summary"]
        run.append_trace(f"orchestrator validation requested another SQL attempt: {validation_summary}")
        loop.validation_attempts += 1
        if not decision.retryable or attempt_idx >= max_validation_retries:
            return loop

    return loop


def _save_validated_result(
    run: WorkflowRun,
    *,
    sql_output: SQLAgentOutput,
    validation_attempts: int,
) -> tuple[str, dict[str, Any]]:
    """Persist one validated SQL result as a SQLite view and return the final workflow result."""
    saved_view = save_view(
        sql_output.candidate_sql,
        DEFAULT_VIEW_NAME,
        database_path=run.database_path,
        replace=True,
    )
    if saved_view.get("status") != "ok":
        return run.result(
            status="error",
            outcome="failed",
            completion_reason="save_failed",
            selected_targets=sql_output.selected_targets,
            candidate_sql=sql_output.candidate_sql,
            sql_result=sql_output.result,
            saved_view_name=DEFAULT_VIEW_NAME,
            saved_view=saved_view,
            last_error=saved_view.get("message", f"Failed to save view {DEFAULT_VIEW_NAME}"),
            validation_feedback=None,
            validation_attempts=validation_attempts,
            trace=_append_trace(run.trace, f"save failed for view {DEFAULT_VIEW_NAME}"),
        )

    return run.result(
        status="saved",
        outcome="fulfilled",
        completion_reason="saved_view",
        selected_targets=sql_output.selected_targets,
        candidate_sql=sql_output.candidate_sql,
        sql_result=sql_output.result,
        saved_view_name=DEFAULT_VIEW_NAME,
        saved_view=saved_view,
        last_error=None,
        validation_feedback=None,
        validation_attempts=validation_attempts,
        trace=_append_trace(run.trace, f"saved result as view {DEFAULT_VIEW_NAME}"),
    )


def _build_sql_failure_result(
    run: WorkflowRun,
    *,
    loop: SqlLoopResult,
) -> tuple[str, dict[str, Any]]:
    """Build the final workflow result for an incomplete, blocked, or invalid SQL run."""
    sql_output = loop.output
    if sql_output is None:
        return run.result(
            status="error",
            outcome="failed",
            completion_reason="sql_execution_failed",
            selected_targets=[],
            candidate_sql=None,
            sql_result=None,
            saved_view_name=None,
            saved_view=None,
            last_error="SQL workflow did not run.",
            validation_feedback=loop.validation_feedback,
            validation_attempts=loop.validation_attempts,
            trace=_append_trace(run.trace, "sql workflow did not run"),
        )

    if sql_output.status != "complete":
        return run.result(
            status=sql_output.status,
            outcome="blocked" if sql_output.status == "blocked" else "failed",
            completion_reason=("sql_blocked" if sql_output.status == "blocked" else "sql_execution_failed"),
            selected_targets=sql_output.selected_targets,
            candidate_sql=sql_output.candidate_sql,
            sql_result=sql_output.result,
            saved_view_name=None,
            saved_view=None,
            last_error=sql_output.last_error,
            validation_feedback=loop.validation_feedback,
            validation_attempts=loop.validation_attempts,
        )

    completion_reason = "validation_attempt_limit"
    outcome = "failed"
    last_error = None
    if loop.validation_feedback:
        last_error = loop.validation_feedback["summary"]
        if not loop.validation_feedback.get("retryable", True):
            completion_reason = "validation_blocked"
            outcome = "blocked"

    return run.result(
        status="error",
        outcome=outcome,
        completion_reason=completion_reason,
        selected_targets=sql_output.selected_targets,
        candidate_sql=sql_output.candidate_sql,
        sql_result=sql_output.result,
        saved_view_name=None,
        saved_view=None,
        last_error=last_error,
        validation_feedback=loop.validation_feedback,
        validation_attempts=loop.validation_attempts,
    )


def _trace_workflow_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Keep LangSmith workflow inputs focused on the public request."""
    return {
        "task": inputs.get("task"),
        "source_files": inputs.get("source_files"),
        "max_prep_trials": inputs.get("max_prep_trials"),
        "max_validation_retries": inputs.get("max_validation_retries"),
        "prompt_provided": bool(str(inputs.get("prompt") or "").strip()),
        "root_dir": None if inputs.get("root_dir") is None else str(inputs["root_dir"]),
    }


def _trace_workflow_outputs(output: WorkflowExecutionResult) -> dict[str, Any]:
    """Keep LangSmith workflow outputs compact and reviewable."""
    return {
        "content": output.content,
        "artifact": output.artifact,
        "agent_artifacts": output.agent_artifacts,
        "active_agent": output.active_agent,
    }


@traceable(
    name="execute_workflow",
    run_type="chain",
    process_inputs=_trace_workflow_inputs,
    process_outputs=_trace_workflow_outputs,
)
def execute_workflow(
    *,
    task: str,
    source_files: list[str],
    max_prep_trials: int = 2,
    max_validation_retries: int = 2,
    prompt: str = "",
    root_dir: str | Path | None = None,
    llm: Any | None = None,
    prep_agent: PrepAgent | None = None,
    sql_agent: SQLAgent | None = None,
    validation_agent: ValidationAgent | None = None,
    config: RunnableConfig | None = None,
) -> WorkflowExecutionResult:
    """Run the full workflow once and return its normalized result."""
    resolved_prep_agent = prep_agent or PrepAgent(
        llm=llm,
        prompt=prompt,
        root_dir=root_dir,
    )
    resolved_sql_agent = sql_agent or (SQLAgent(llm=llm) if llm is not None else SQLAgent())
    resolved_validation_agent = validation_agent or (ValidationAgent(llm=llm) if llm is not None else ValidationAgent())

    run = WorkflowRun(
        task=task,
        source_files=source_files,
        skill_payload=build_worker_skill_payload(
            task,
            config=patch_config(config, run_name="skills_context"),
        ),
    )
    agent_artifacts: dict[str, dict[str, Any]] = {}
    prep_output = resolved_prep_agent.invoke(
        run.task,
        source_files=run.source_files,
        worker_instructions=run.skill_payload.worker_instructions,
        skill_refs=run.skill_payload.skill_refs,
        max_prep_trials=max_prep_trials,
        config=patch_config(config, run_name="prep_agent"),
    )
    agent_artifacts[PREP_AGENT_NAME] = prep_output.model_dump(mode="json")
    run.database_path = prep_output.database_path
    run.extracted_targets = prep_output.extracted_targets
    run.append_trace(*prep_output.trace)

    if prep_output.status != "prepared":
        content, artifact = run.result(
            status="error",
            outcome="failed",
            completion_reason="prep_failed",
            selected_targets=[],
            candidate_sql=None,
            sql_result=None,
            saved_view_name=None,
            saved_view=None,
            last_error=prep_output.last_error or "Preparation failed.",
            validation_feedback=None,
            validation_attempts=0,
        )
        return WorkflowExecutionResult(
            content=content,
            artifact=artifact,
            agent_artifacts=agent_artifacts,
            active_agent=PREP_AGENT_NAME,
        )

    loop = _run_sql_validation_loop(
        run,
        prompt=prompt,
        sql_agent=resolved_sql_agent,
        validation_agent=resolved_validation_agent,
        max_validation_retries=max_validation_retries,
        config=config,
    )
    if loop.output is not None:
        agent_artifacts[SQL_AGENT_NAME] = loop.output.model_dump(mode="json")
    if loop.validation_output is not None:
        agent_artifacts[VALIDATION_AGENT_NAME] = loop.validation_output.model_dump(mode="json")

    if loop.validated and loop.output is not None:
        content, artifact = _save_validated_result(
            run,
            sql_output=loop.output,
            validation_attempts=loop.validation_attempts,
        )
        return WorkflowExecutionResult(
            content=content,
            artifact=artifact,
            agent_artifacts=agent_artifacts,
            active_agent=None,
        )

    content, artifact = _build_sql_failure_result(run, loop=loop)
    return WorkflowExecutionResult(
        content=content,
        artifact=artifact,
        agent_artifacts=agent_artifacts,
        active_agent=(VALIDATION_AGENT_NAME if loop.validation_output is not None else SQL_AGENT_NAME),
    )
