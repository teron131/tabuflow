"""Stage-bridged orchestrator graph for local data analysis."""

from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import patch_config
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langsmith import traceable
from pydantic import BaseModel, Field

from ...tools.sql.query import save_view
from ..base import ApplicationAgent
from ..prep_agent import PrepAgent, PrepTaskOutput
from ..sql_agent import SQLAgent, SQLAgentOutput
from ..validation_agent import ValidationAgent, ValidationOutput
from .payloads import build_result_artifact, build_result_message
from .skill_context import WorkerSkillPayload, build_worker_skill_payload, format_skill_sql_references, summarize_skill_refs

DEFAULT_VIEW_NAME = "analysis_result"
MAX_VIEW_TASK_SLUG_CHARS = 48
PREP_AGENT_NAME = "prep_agent"
SQL_AGENT_NAME = "sql_agent"
VALIDATION_AGENT_NAME = "validation_agent"
VIEW_TASK_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


@dataclass
class OrchestratorRun:
    """Shared orchestrator run state used across prep, SQL, and save stages."""

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
        """Build the caller-facing result from the current run state."""
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
class OrchestratorExecutionResult:
    """Normalized result returned by direct and tool-backed orchestrator execution."""

    content: str
    artifact: dict[str, Any]
    agent_artifacts: dict[str, dict[str, Any]]
    active_agent: str | None


class OrchestratorInput(BaseModel):
    """Public input schema for the stage-bridged orchestrator graph."""

    task: str
    source_files: list[str]
    max_prep_trials: int = 2
    max_validation_retries: int = 2


class OrchestratorOutput(BaseModel):
    """Public output schema for the stage-bridged orchestrator graph."""

    content: str = ""
    artifact: dict[str, Any] = Field(default_factory=dict)
    agent_artifacts: dict[str, dict[str, Any]] = Field(default_factory=dict)
    active_agent: str | None = None


class OrchestratorState(OrchestratorInput, OrchestratorOutput):
    """Parent graph state that bridges the worker subgraph stages."""

    run_id: str = Field(default_factory=lambda: uuid4().hex[:8])
    worker_instructions: str = ""
    skill_refs: list[dict[str, Any]] = Field(default_factory=list)
    trace: list[str] = Field(default_factory=list)
    database_path: str | None = None
    extracted_targets: list[dict[str, Any]] = Field(default_factory=list)
    prep_output: dict[str, Any] | None = None
    sql_output: dict[str, Any] | None = None
    validation_output: dict[str, Any] | None = None
    validation_feedback: dict[str, Any] | None = None
    validation_attempts: int = 0
    validated: bool = False


def _append_trace(trace: list[str], message: str) -> list[str]:
    """Append one trace message while keeping the trace compact."""
    return [*trace, message][-12:]


def _append_trace_messages(trace: list[str], messages: list[str]) -> list[str]:
    """Append multiple trace messages while preserving the bounded trace shape."""
    next_trace = trace
    for message in messages:
        next_trace = _append_trace(next_trace, message)
    return next_trace


def _orchestrator_run_from_state(state: OrchestratorState) -> OrchestratorRun:
    """Rebuild the result-shaping run object from graph state."""
    return OrchestratorRun(
        task=state.task,
        source_files=state.source_files,
        skill_payload=WorkerSkillPayload(
            worker_instructions=state.worker_instructions,
            skill_refs=state.skill_refs,
        ),
        run_id=state.run_id,
        trace=state.trace,
        database_path=state.database_path,
        extracted_targets=state.extracted_targets,
    )


def _sql_loop_from_state(state: OrchestratorState) -> SqlLoopResult:
    """Rebuild SQL loop state from parent graph fields."""
    return SqlLoopResult(
        output=(None if state.sql_output is None else SQLAgentOutput.model_validate(state.sql_output)),
        validation_output=(None if state.validation_output is None else ValidationOutput.model_validate(state.validation_output)),
        validation_feedback=state.validation_feedback,
        validation_attempts=state.validation_attempts,
        validated=state.validated,
    )


def _orchestrator_result_update(
    *,
    content: str,
    artifact: dict[str, Any],
    agent_artifacts: dict[str, dict[str, Any]],
    active_agent: str | None,
) -> dict[str, Any]:
    """Return the graph update that satisfies the orchestrator output schema."""
    return {
        "content": content,
        "artifact": artifact,
        "agent_artifacts": agent_artifacts,
        "active_agent": active_agent,
    }


def _task_view_slug(task: str) -> str:
    """Return a SQLite-safe slug for an orchestrator task."""
    slug = VIEW_TASK_SLUG_PATTERN.sub("_", task.lower()).strip("_")
    bounded_slug = slug[:MAX_VIEW_TASK_SLUG_CHARS].strip("_")
    return bounded_slug or "run"


def _orchestrator_view_name(run: OrchestratorRun) -> str:
    """Return the per-run saved result view name."""
    return f"{DEFAULT_VIEW_NAME}_{_task_view_slug(run.task)}_{run.run_id}"


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


def _build_sql_worker_context(
    prompt: str,
    *,
    worker_instructions: str,
    skill_refs: list[dict[str, Any]],
) -> str:
    """Build context that should inform SQL planning without redefining the task."""
    parts = [prompt.strip()] if prompt.strip() else []
    if worker_instructions.strip():
        parts.append(worker_instructions.strip())
    if skill_ref_summary := summarize_skill_refs(skill_refs):
        parts.append(skill_ref_summary)
    if sql_references := format_skill_sql_references(skill_refs):
        parts.append(sql_references)
    return "\n\n".join(parts)


def _save_validated_result(
    run: OrchestratorRun,
    *,
    sql_output: SQLAgentOutput,
    validation_attempts: int,
) -> tuple[str, dict[str, Any]]:
    """Persist one validated SQL result as a SQLite view and return the final result."""
    view_name = _orchestrator_view_name(run)
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
    run: OrchestratorRun,
    *,
    loop: SqlLoopResult,
) -> tuple[str, dict[str, Any]]:
    """Build the final result for an incomplete, blocked, or invalid SQL run."""
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
            last_error="SQL agent did not run.",
            validation_feedback=loop.validation_feedback,
            validation_attempts=loop.validation_attempts,
            trace=_append_trace(run.trace, "sql agent did not run"),
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


class Orchestrator(ApplicationAgent):
    """Top-level orchestrator whose graph is the staged data-analysis flow."""

    default_model_order = ("main_llm", "fast_llm", "quality_llm")

    def __init__(
        self,
        *,
        prompt: str = "",
        root_dir: str | Path | None = None,
        llm: Any | None = None,
    ):
        super().__init__(llm=llm)
        self.prompt = prompt
        self.root_dir = root_dir
        self.graph = self.build_graph()
        self.graph_artifacts = self.write_graph_artifacts(
            self.graph,
            filename_stem="orchestrator-graph",
        )

    def build_graph(self) -> CompiledStateGraph:
        """Build the compiled orchestrator graph."""
        return build_orchestrator_graph(
            prompt=self.prompt,
            root_dir=self.root_dir,
            llm=self.llm,
        )

    def invoke(
        self,
        task: str | OrchestratorInput | dict[str, Any],
        *,
        source_files: list[str] | None = None,
        max_prep_trials: int = 2,
        max_validation_retries: int = 2,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """Run one orchestrator graph invocation."""
        if isinstance(task, OrchestratorInput):
            payload = task
        elif isinstance(task, dict):
            payload = OrchestratorInput.model_validate(task)
        else:
            payload = OrchestratorInput(
                task=task,
                source_files=source_files or [],
                max_prep_trials=max_prep_trials,
                max_validation_retries=max_validation_retries,
            )
        return self.graph.invoke(payload, config=config)

    def answer(
        self,
        task: str | OrchestratorInput | dict[str, Any],
        *,
        source_files: list[str] | None = None,
        max_prep_trials: int = 2,
        max_validation_retries: int = 2,
        config: RunnableConfig | None = None,
    ) -> str:
        """Return the final content for one orchestrator graph invocation."""
        result = self.invoke(
            task,
            source_files=source_files,
            max_prep_trials=max_prep_trials,
            max_validation_retries=max_validation_retries,
            config=config,
        )
        output = OrchestratorOutput.model_validate(result)
        return output.content


def build_orchestrator_graph(
    *,
    prompt: str = "",
    root_dir: str | Path | None = None,
    llm: Any | None = None,
    prep_agent: PrepAgent | None = None,
    sql_agent: SQLAgent | None = None,
    validation_agent: ValidationAgent | None = None,
    name: str = "orchestrator",
) -> CompiledStateGraph:
    """Build the parent orchestrator graph that bridges worker subgraph stages."""
    resolved_prep_agent = prep_agent or PrepAgent(
        llm=llm,
        prompt=prompt,
        root_dir=root_dir,
    )
    resolved_sql_agent = sql_agent or (SQLAgent(llm=llm) if llm is not None else SQLAgent())
    resolved_validation_agent = validation_agent or (ValidationAgent(llm=llm) if llm is not None else ValidationAgent())

    def skill_context_stage(
        state: OrchestratorState,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """Load task-relevant skill context into the parent orchestrator state."""
        skill_payload = build_worker_skill_payload(
            state.task,
            config=patch_config(config, run_name="skills_context"),
        )
        return {
            "worker_instructions": skill_payload.worker_instructions,
            "skill_refs": skill_payload.skill_refs,
        }

    def prep_stage(
        state: OrchestratorState,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """Bridge parent orchestrator state into the prep ReAct subgraph."""
        prep_output = resolved_prep_agent.invoke(
            state.task,
            source_files=state.source_files,
            worker_instructions=state.worker_instructions,
            skill_refs=state.skill_refs,
            max_prep_trials=state.max_prep_trials,
            config=patch_config(config, run_name=PREP_AGENT_NAME),
        )
        prep_artifact = prep_output.model_dump(mode="json")
        agent_artifacts = {**state.agent_artifacts, PREP_AGENT_NAME: prep_artifact}
        return {
            "prep_output": prep_artifact,
            "agent_artifacts": agent_artifacts,
            "database_path": prep_output.database_path,
            "extracted_targets": prep_output.extracted_targets,
            "trace": _append_trace_messages(state.trace, prep_output.trace),
            "active_agent": PREP_AGENT_NAME,
        }

    def build_prep_subgraph() -> CompiledStateGraph:
        """Build the prep stage as a named orchestrator subgraph."""
        builder = StateGraph(OrchestratorState)
        builder.add_node("prep_agent", prep_stage)
        builder.add_edge(START, "prep_agent")
        builder.add_edge("prep_agent", END)
        return builder.compile(name="prep")

    def sql_stage(
        state: OrchestratorState,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """Run one SQL worker stage after prep has produced query targets."""
        worker_context = _build_sql_worker_context(
            prompt,
            worker_instructions=state.worker_instructions,
            skill_refs=state.skill_refs,
        )
        sql_output = resolved_sql_agent.invoke(
            state.task,
            database_path=state.database_path,
            preferred_targets=_preferred_sql_targets(state.extracted_targets),
            source_files=state.source_files,
            worker_context=worker_context,
            skill_refs=state.skill_refs,
            validation_feedback=state.validation_feedback,
            config=patch_config(config, run_name=f"sql_agent_attempt_{state.validation_attempts + 1}"),
        )
        sql_artifact = sql_output.model_dump(mode="json")
        agent_artifacts = {**state.agent_artifacts, SQL_AGENT_NAME: sql_artifact}
        return {
            "sql_output": sql_artifact,
            "agent_artifacts": agent_artifacts,
            "trace": _append_trace_messages(state.trace, sql_output.trace),
            "active_agent": SQL_AGENT_NAME,
        }

    def build_sql_subgraph() -> CompiledStateGraph:
        """Build the SQL agent as a named orchestrator subgraph."""
        builder = StateGraph(OrchestratorState)
        builder.add_node("sql_agent", sql_stage)
        builder.add_edge(START, "sql_agent")
        builder.add_edge("sql_agent", END)
        return builder.compile(name="sql")

    def validation_stage(
        state: OrchestratorState,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """Validate the SQL result before saving a view."""
        sql_output = SQLAgentOutput.model_validate(state.sql_output)
        validation_output = resolved_validation_agent.invoke(
            task=state.task,
            source_files=state.source_files,
            extracted_targets=state.extracted_targets,
            selected_targets=sql_output.selected_targets,
            candidate_sql=sql_output.candidate_sql,
            sql_result=sql_output.result,
            previous_feedback=state.validation_feedback,
            validation_attempts=state.validation_attempts,
            config=patch_config(config, run_name=f"validation_agent_attempt_{state.validation_attempts + 1}"),
        )
        validation_artifact = validation_output.model_dump(mode="json")
        agent_artifacts = {
            **state.agent_artifacts,
            VALIDATION_AGENT_NAME: validation_artifact,
        }
        if validation_output.valid:
            return {
                "validation_output": validation_artifact,
                "agent_artifacts": agent_artifacts,
                "validated": True,
                "validation_feedback": None,
                "trace": _append_trace(state.trace, "orchestrator validation accepted the SQL result"),
                "active_agent": None,
            }

        summary = validation_output.summary.strip() or "The SQL result does not appear to fully satisfy the task."
        instructions = [instruction.strip() for instruction in validation_output.instructions if instruction.strip()]
        validation_feedback = {
            "retryable": validation_output.retryable,
            "summary": summary,
            "instructions": instructions or [summary],
        }
        return {
            "validation_output": validation_artifact,
            "agent_artifacts": agent_artifacts,
            "validation_feedback": validation_feedback,
            "validation_attempts": state.validation_attempts + 1,
            "trace": _append_trace(state.trace, f"orchestrator validation requested another SQL attempt: {summary}"),
            "active_agent": VALIDATION_AGENT_NAME,
        }

    def save_stage(state: OrchestratorState) -> dict[str, Any]:
        """Persist a validated SQL result as the terminal orchestrator artifact."""
        if state.sql_output is None:
            content, artifact = _build_sql_failure_result(
                _orchestrator_run_from_state(state),
                loop=_sql_loop_from_state(state),
            )
        else:
            content, artifact = _save_validated_result(
                _orchestrator_run_from_state(state),
                sql_output=SQLAgentOutput.model_validate(state.sql_output),
                validation_attempts=state.validation_attempts,
            )
        return _orchestrator_result_update(
            content=content,
            artifact=artifact,
            agent_artifacts=state.agent_artifacts,
            active_agent=None,
        )

    def finalize_stage(state: OrchestratorState) -> dict[str, Any]:
        """Build the terminal orchestrator artifact for blocked or failed runs."""
        if state.content and state.artifact:
            return _orchestrator_result_update(
                content=state.content,
                artifact=state.artifact,
                agent_artifacts=state.agent_artifacts,
                active_agent=state.active_agent,
            )

        run = _orchestrator_run_from_state(state)
        prep_output = None if state.prep_output is None else PrepTaskOutput.model_validate(state.prep_output)
        if prep_output is None or prep_output.status != "prepared":
            content, artifact = run.result(
                status="error",
                outcome="failed",
                completion_reason="prep_failed",
                selected_targets=[],
                candidate_sql=None,
                sql_result=None,
                saved_view_name=None,
                saved_view=None,
                last_error=(None if prep_output is None else prep_output.last_error) or "Preparation failed.",
                validation_feedback=None,
                validation_attempts=0,
            )
            return _orchestrator_result_update(
                content=content,
                artifact=artifact,
                agent_artifacts=state.agent_artifacts,
                active_agent=PREP_AGENT_NAME,
            )

        content, artifact = _build_sql_failure_result(run, loop=_sql_loop_from_state(state))
        return _orchestrator_result_update(
            content=content,
            artifact=artifact,
            agent_artifacts=state.agent_artifacts,
            active_agent=(VALIDATION_AGENT_NAME if state.validation_output is not None else SQL_AGENT_NAME),
        )

    def route_after_prep(state: OrchestratorState) -> str:
        """Route to SQL only after prep produces a usable database target."""
        prep_output = None if state.prep_output is None else PrepTaskOutput.model_validate(state.prep_output)
        return "sql" if prep_output is not None and prep_output.status == "prepared" else "finalize"

    def route_after_sql(state: OrchestratorState) -> str:
        """Route completed SQL results to validation, otherwise finalize."""
        sql_output = None if state.sql_output is None else SQLAgentOutput.model_validate(state.sql_output)
        return "validate" if sql_output is not None and sql_output.status == "complete" else "finalize"

    def route_after_validation(state: OrchestratorState) -> str:
        """Route validation acceptance, retry, or terminal failure."""
        if state.validated:
            return "save"
        if not state.validation_feedback or not state.validation_feedback.get("retryable", True):
            return "finalize"
        if state.validation_attempts <= state.max_validation_retries:
            return "sql"
        return "finalize"

    builder = StateGraph(
        OrchestratorState,
        input_schema=OrchestratorInput,
        output_schema=OrchestratorOutput,
    )
    builder.add_node("skill_context", skill_context_stage)
    builder.add_node("prep", build_prep_subgraph())
    builder.add_node("sql", build_sql_subgraph())
    builder.add_node("validate", validation_stage)
    builder.add_node("save", save_stage)
    builder.add_node("finalize", finalize_stage)
    builder.add_edge(START, "skill_context")
    builder.add_edge("skill_context", "prep")
    builder.add_conditional_edges(
        "prep",
        route_after_prep,
        {
            "sql": "sql",
            "finalize": "finalize",
        },
    )
    builder.add_conditional_edges(
        "sql",
        route_after_sql,
        {
            "validate": "validate",
            "finalize": "finalize",
        },
    )
    builder.add_conditional_edges(
        "validate",
        route_after_validation,
        {
            "save": "save",
            "sql": "sql",
            "finalize": "finalize",
        },
    )
    builder.add_edge("save", END)
    builder.add_edge("finalize", END)
    return builder.compile(name=name)


def _trace_orchestrator_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Keep LangSmith orchestrator inputs focused on the public request."""
    return {
        "task": inputs.get("task"),
        "source_files": inputs.get("source_files"),
        "max_prep_trials": inputs.get("max_prep_trials"),
        "max_validation_retries": inputs.get("max_validation_retries"),
        "prompt_provided": bool(str(inputs.get("prompt") or "").strip()),
        "root_dir": None if inputs.get("root_dir") is None else str(inputs["root_dir"]),
    }


def _trace_orchestrator_outputs(output: OrchestratorExecutionResult) -> dict[str, Any]:
    """Keep LangSmith orchestrator outputs compact and reviewable."""
    return {
        "content": output.content,
        "artifact": output.artifact,
        "agent_artifacts": output.agent_artifacts,
        "active_agent": output.active_agent,
    }


@traceable(
    name="execute_orchestrator",
    run_type="chain",
    process_inputs=_trace_orchestrator_inputs,
    process_outputs=_trace_orchestrator_outputs,
)
def execute_orchestrator(
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
) -> OrchestratorExecutionResult:
    """Run the full orchestrator once and return its normalized result."""
    graph = build_orchestrator_graph(
        prompt=prompt,
        root_dir=root_dir,
        llm=llm,
        prep_agent=prep_agent,
        sql_agent=sql_agent,
        validation_agent=validation_agent,
    )
    result = graph.invoke(
        OrchestratorInput(
            task=task,
            source_files=source_files,
            max_prep_trials=max_prep_trials,
            max_validation_retries=max_validation_retries,
        ),
        config=config,
    )
    output = OrchestratorOutput.model_validate(result)
    return OrchestratorExecutionResult(**output.model_dump(mode="python"))
