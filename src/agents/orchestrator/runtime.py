"""Runtime state adapters and graph-update helpers for orchestrator stages."""

from dataclasses import dataclass, field
import re
from typing import Any
from uuid import uuid4

from ...tools.sql.query import save_view
from ..sql_agent import SQLAgentOutput
from .payloads import build_result_artifact, build_result_message
from .state import OrchestratorState

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
    run_id: str = field(default_factory=lambda: uuid4().hex[:8])
    trace: list[str] = field(default_factory=list)
    database_path: str | None = None
    extracted_targets: list[dict[str, Any]] = field(default_factory=list)

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
    validation_feedback: dict[str, Any] | None = None
    validation_attempts: int = 0


def append_trace(trace: list[str], message: str) -> list[str]:
    """Append one trace message while keeping the trace compact."""
    return [*trace, message][-12:]


def append_trace_messages(trace: list[str], messages: list[str]) -> list[str]:
    """Append multiple trace messages while preserving the bounded trace shape."""
    next_trace = trace
    for message in messages:
        next_trace = append_trace(next_trace, message)
    return next_trace


def orchestrator_run_from_state(state: OrchestratorState) -> OrchestratorRun:
    """Rebuild the result-shaping run object from graph state."""
    return OrchestratorRun(
        task=state.task,
        source_files=state.source_files,
        run_id=state.run_id,
        trace=state.trace,
        database_path=state.database_path,
        extracted_targets=state.extracted_targets,
    )


def sql_loop_from_state(state: OrchestratorState) -> SqlLoopResult:
    """Rebuild SQL loop state from parent graph fields."""
    return SqlLoopResult(
        output=sql_output_from_state(state),
        validation_feedback=state.validation_feedback,
        validation_attempts=state.validation_attempts,
    )


def sql_output_from_state(state: OrchestratorState) -> SQLAgentOutput | None:
    """Rebuild the SQL agent output from direct subgraph state fields."""
    has_direct_sql_output = state.status != "pending" or state.attempts > 0 or state.result is not None or state.candidate_sql is not None
    if has_direct_sql_output:
        return SQLAgentOutput(
            status=state.status,
            selected_targets=state.selected_targets,
            candidate_sql=state.candidate_sql,
            repair_hints=state.repair_hints,
            result=state.result,
            attempts=state.attempts,
            rationale=state.rationale,
            last_error=state.last_error,
            trace=state.trace,
        )
    if state.sql_output is not None:
        return SQLAgentOutput.model_validate(state.sql_output)
    return None


def reset_sql_attempt_update() -> dict[str, Any]:
    """Reset SQL graph fields before routing into another SQL attempt."""
    return {
        "status": "pending",
        "selected_targets": [],
        "candidate_sql": None,
        "repair_hints": [],
        "result": None,
        "attempts": 0,
        "rationale": None,
        "last_error": None,
        "suggestions": [],
        "inspected_targets": [],
        "plan": None,
        "repair_count": 0,
    }


def preferred_sql_targets(extracted_targets: list[dict[str, Any]]) -> list[str]:
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


def orchestrator_result_update(
    *,
    content: str,
    artifact: dict,
    agent_artifacts: dict[str, dict],
    active_agent: str | None,
) -> dict:
    """Return the graph update that satisfies the orchestrator output schema."""
    return {
        "content": content,
        "artifact": artifact,
        "agent_artifacts": agent_artifacts,
        "active_agent": active_agent,
    }


def task_view_slug(task: str) -> str:
    """Return a SQLite-safe slug for an orchestrator task."""
    slug = VIEW_TASK_SLUG_PATTERN.sub("_", task.lower()).strip("_")
    bounded_slug = slug[:MAX_VIEW_TASK_SLUG_CHARS].strip("_")
    return bounded_slug or "run"


def orchestrator_view_name(run: OrchestratorRun) -> str:
    """Return the per-run saved result view name."""
    return f"{DEFAULT_VIEW_NAME}_{task_view_slug(run.task)}_{run.run_id}"


def save_validated_result(
    run: OrchestratorRun,
    *,
    sql_output: SQLAgentOutput,
    validation_attempts: int,
) -> tuple[str, dict]:
    """Persist one validated SQL result as a SQLite view and return the final result."""
    view_name = orchestrator_view_name(run)
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
            trace=append_trace(run.trace, f"save failed for view {view_name}"),
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
        trace=append_trace(run.trace, f"saved result as view {view_name}"),
    )


def build_sql_failure_result(
    run: OrchestratorRun,
    *,
    loop: SqlLoopResult,
) -> tuple[str, dict]:
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
            trace=append_trace(run.trace, "sql agent did not run"),
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
