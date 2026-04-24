"""Tool wrappers exposed to the top-level orchestrator graph."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
from typing import Annotated, Any
from uuid import uuid4

from langchain.messages import ToolMessage
from langchain.tools import BaseTool, tool
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import patch_config
from langchain_core.tools import InjectedToolArg, InjectedToolCallId
from langgraph.types import Command
from langsmith import traceable

from ...tools import list_skills, load_skills, search_skills
from ...tools.sql.query import save_view
from ..prep_agent import PrepAgent
from ..sql_agent import SQLAgent, SQLAgentOutput
from ..validation_agent import ValidationAgent, ValidationOutput
from .payloads import build_result_artifact, build_result_message

SKILLS_PATH = "skills"
DEFAULT_VIEW_NAME = "analysis_result"
VIEW_NAME_STOP_WORDS = {"a", "an", "and", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
MAX_SKILL_REF_PREVIEW = 8
PREP_AGENT_NAME = "prep_agent"
SQL_AGENT_NAME = "sql_agent"
VALIDATION_AGENT_NAME = "validation_agent"


@dataclass
class WorkerSkillPayload:
    """Worker-facing skill instructions and loaded references for one run."""

    worker_instructions: str = ""
    skill_refs: list[dict[str, Any]] = field(default_factory=list)


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


def list_skills_context(
    *,
    path: str = SKILLS_PATH,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Return the raw workspace-skills listing payload."""
    return list_skills.invoke({"path": path}, config=config)


def search_skills_context(
    query: str,
    *,
    path: str = SKILLS_PATH,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Return the raw semantic workspace-skills search payload."""
    return search_skills.invoke(
        {
            "path": path,
            "query": query,
        },
        config=config,
    )


def format_skills_overview(result: dict[str, Any]) -> str:
    """Render a deterministic system-prompt section for available skills."""
    if result.get("status") == "error":
        return "Workspace skills available under `skills`:\n- unavailable"

    diagnostics = [str(item) for item in result.get("diagnostics", [])]
    skills = list(result.get("skills", []))
    lines = ["Workspace skills available under `skills`:"]
    if skills:
        for skill in skills:
            skill_name = skill.get("name", "unknown")
            description = str(skill.get("description", "")).strip()
            skill_path = skill.get("path", "")
            lines.append(f"- {skill_name}: {description} ({skill_path})")
    else:
        lines.append("- none found")

    if diagnostics:
        lines.append(f"Diagnostics: {'; '.join(diagnostics[:3])}")
    return "\n".join(lines)


def format_skill_matches(result: dict[str, Any]) -> str:
    """Render a deterministic user-turn section for relevant skills."""
    if result.get("status") == "error":
        return "- unavailable"

    diagnostics = [str(item) for item in result.get("diagnostics", [])]
    skills = list(result.get("skills", []))
    lines = []
    if skills:
        for skill in skills:
            score = skill.get("score")
            suffix = f", score={score}" if score is not None else ""
            skill_name = skill.get("name", "unknown")
            description = str(skill.get("description", "")).strip()
            skill_path = skill.get("path", "")
            lines.append(f"- {skill_name}: {description} ({skill_path}{suffix})")
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
    config: RunnableConfig | None = None,
) -> WorkerSkillPayload:
    """Search and load matched skills into one worker-ready payload."""
    search_result = search_skills_context(task, path=path, config=config)
    matched_skills = list(search_result.get("skills", []))
    worker_sections: list[str] = []
    skill_refs: list[dict[str, Any]] = []
    for skill in matched_skills:
        skill_name = str(skill.get("name", "")).strip()
        if not skill_name:
            continue

        load_result = load_skills.invoke(
            {
                "path": path,
                "skills": skill_name,
            },
            config=config,
        )
        loaded_skills = list(load_result.get("skills", []))
        if not loaded_skills:
            continue
        loaded_skill = loaded_skills[0]

        description = str(loaded_skill.get("description", "")).strip()
        instructions_payload = loaded_skill.get("instructions") or {}
        instructions = str(instructions_payload.get("content", "")).strip()
        if instructions:
            skill_title = f"Skill `{loaded_skill.get('name', 'unknown')}`: {description}"
            worker_sections.append(skill_title.strip())
            worker_sections.append(instructions)
        skill_refs.append(loaded_skill)

    return WorkerSkillPayload(
        worker_instructions="\n\n".join(section for section in worker_sections if section.strip()),
        skill_refs=skill_refs,
    )


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
    source_file_list = "\n".join(f"- {source_file}" for source_file in source_files) or "- (none)"
    parts.append(f"Source files:\n{source_file_list}\nTask: {task}")
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


def _agent_command(
    *,
    tool_name: str,
    tool_call_id: str,
    content: str,
    latest_artifact: dict[str, Any],
    active_agent: str | None,
    workflow_artifact: dict[str, Any] | None = None,
    agent_artifacts: dict[str, dict[str, Any]] | None = None,
) -> Command:
    """Return one tool command that updates orchestrator state plus tool history."""
    update: dict[str, Any] = {
        "messages": [
            ToolMessage(
                content=content,
                tool_call_id=tool_call_id,
                name=tool_name,
            )
        ],
        "latest_artifact": latest_artifact,
        "active_agent": active_agent,
    }
    if workflow_artifact is not None:
        update["workflow_artifact"] = workflow_artifact
    if agent_artifacts is not None:
        update["agent_artifacts"] = agent_artifacts
    return Command(update=update)


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
    view_name = _build_saved_view_name(run.task, run.run_id)
    saved_view = save_view(
        sql_output.candidate_sql,
        view_name,
        database_path=run.database_path,
        replace=False,
    )
    if saved_view.get("status") != "ok":
        return run.result(
            status="error",
            outcome="failed",
            completion_reason="save_failed",
            selected_targets=sql_output.selected_targets,
            candidate_sql=sql_output.candidate_sql,
            sql_result=sql_output.result,
            saved_view_name=view_name,
            saved_view=saved_view,
            last_error=saved_view.get("message", f"Failed to save view {view_name}"),
            validation_feedback=None,
            validation_attempts=validation_attempts,
            trace=_append_trace(run.trace, f"save failed for view {view_name}"),
        )

    return run.result(
        status="saved",
        outcome="fulfilled",
        completion_reason="saved_view",
        selected_targets=sql_output.selected_targets,
        candidate_sql=sql_output.candidate_sql,
        sql_result=sql_output.result,
        saved_view_name=view_name,
        saved_view=saved_view,
        last_error=None,
        validation_feedback=None,
        validation_attempts=validation_attempts,
        trace=_append_trace(run.trace, f"saved result as view {view_name}"),
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
        skill_payload=_build_worker_skill_payload(
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


def make_orchestrator_tools(
    *,
    prompt: str = "",
    root_dir: str | Path | None = None,
    llm: Any | None = None,
) -> list[BaseTool]:
    """Build the static tool set exposed to the top-level orchestrator."""
    prep_agent: PrepAgent | None = None
    sql_agent: SQLAgent | None = None
    validation_agent: ValidationAgent | None = None

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

    def get_validation_agent() -> ValidationAgent:
        """Return one cached validation worker instance."""
        nonlocal validation_agent
        if validation_agent is None:
            validation_agent = ValidationAgent(llm=llm) if llm is not None else ValidationAgent()
        return validation_agent

    @tool(parse_docstring=True)
    def run_sql_workflow(
        question: str,
        database_path: str,
        max_suggestions: int = 3,
        max_repairs: int = 2,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> Command:
        """Run the SQL agent workflow against an existing SQLite database.

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
            config=patch_config(config, run_name="sql_agent"),
        )
        artifact = output.model_dump(mode="json")
        return _agent_command(
            tool_name="run_sql_workflow",
            tool_call_id=tool_call_id,
            content=_summarize_sql_output(artifact),
            latest_artifact=artifact,
            active_agent=SQL_AGENT_NAME,
            agent_artifacts={SQL_AGENT_NAME: artifact},
        )

    @tool(parse_docstring=True)
    def run_workflow(
        task: str,
        source_files: list[str],
        max_prep_trials: int = 2,
        max_validation_retries: int = 2,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> Command:
        """Run the workflow on local files and return its final result.

        Args:
            task: Natural-language task to execute against the supplied files.
            source_files: One or more local spreadsheet or table-like file paths.
            max_prep_trials: Maximum number of prep-agent retries before stopping.
            max_validation_retries: Maximum number of validator-requested SQL retries.
        """
        workflow_result = execute_workflow(
            task=task,
            source_files=source_files,
            max_prep_trials=max_prep_trials,
            max_validation_retries=max_validation_retries,
            prompt=prompt,
            root_dir=root_dir,
            llm=llm,
            prep_agent=get_prep_agent(),
            sql_agent=get_sql_agent(),
            validation_agent=get_validation_agent(),
            config=config,
        )
        return _agent_command(
            tool_name="run_workflow",
            tool_call_id=tool_call_id,
            content=workflow_result.content,
            latest_artifact=workflow_result.artifact,
            active_agent=workflow_result.active_agent,
            workflow_artifact=workflow_result.artifact,
            agent_artifacts=workflow_result.agent_artifacts,
        )

    return [
        load_skills,
        run_sql_workflow,
        run_workflow,
    ]
