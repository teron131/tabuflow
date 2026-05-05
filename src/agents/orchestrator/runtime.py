"""Runtime state adapters and graph-update helpers for orchestrator stages."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import uuid4

from ...pipelines.namer import name_sql_artifact
from ...tools.sql.query import save_view
from ..trace_utils import SAVE_STAGE, append_stage_trace
from .payloads import build_result_artifact, build_result_message
from .state import OrchestratorState, SQLArtifactState, latest_user_message

PREP_STAGE_NAME = "prep_stage"
QUERY_STAGE_NAME = "query_stage"
VALIDATION_STAGE_NAME = "validation_stage"


@dataclass
class OrchestratorRun:
    """Shared orchestrator run state used across prep, SQL, and save stages."""

    message: str
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
        sql_path: str | None,
        last_error: str | None,
        validation_feedback: dict[str, Any] | None,
        validation_attempts: int,
        trace: list[str] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Build the caller-facing result from the current run state."""
        artifact = build_result_artifact(
            message=self.message,
            status=status,
            outcome=outcome,
            completion_reason=completion_reason,
            source_files=self.source_files,
            database_path=self.database_path,
            sql_path=sql_path,
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

    output: SQLArtifactState | None = None
    validation_feedback: dict[str, Any] | None = None
    validation_attempts: int = 0


def orchestrator_run_from_state(state: OrchestratorState) -> OrchestratorRun:
    """Rebuild the result-shaping run object from graph state."""
    return OrchestratorRun(
        message=latest_user_message(state.messages),
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


def sql_output_from_state(state: OrchestratorState) -> SQLArtifactState | None:
    """Rebuild the SQL stage output from direct orchestrator state fields."""
    has_direct_sql_output = state.status != "pending" or state.attempts > 0 or state.result is not None or state.candidate_sql is not None
    if not has_direct_sql_output:
        return None
    return SQLArtifactState(
        status=state.status,
        sql_path=state.sql_path,
        selected_targets=state.selected_targets,
        candidate_sql=state.candidate_sql,
        repair_hints=state.repair_hints,
        result=state.result,
        attempts=state.attempts,
        last_error=state.last_error,
        trace=state.trace,
    )


def reset_sql_attempt_update() -> dict[str, Any]:
    """Reset SQL stage fields before routing into another SQL attempt."""
    return {
        "status": "pending",
        "selected_targets": [],
        "candidate_sql": None,
        "repair_hints": [],
        "result": None,
        "attempts": 0,
        "last_error": None,
        "sql_hashlines": None,
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


def orchestrator_view_name(
    run: OrchestratorRun,
    *,
    sql_path: str | None = None,
) -> str:
    """Return the per-run saved result view name."""
    return Path(sql_path).stem if sql_path else name_sql_artifact(run.message, run.run_id)


def _sql_output_result_fields(
    sql_output: SQLArtifactState,
    *,
    saved_view_name: str | None = None,
    saved_view: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return result fields carried by one SQL-stage output."""
    return {
        "selected_targets": sql_output.selected_targets,
        "candidate_sql": sql_output.candidate_sql,
        "sql_result": sql_output.result,
        "saved_view_name": saved_view_name,
        "saved_view": saved_view,
        "sql_path": sql_output.sql_path,
    }


def save_sql_result(
    run: OrchestratorRun,
    *,
    sql_output: SQLArtifactState,
    validation_feedback: dict[str, Any] | None,
    validation_attempts: int,
) -> tuple[str, dict]:
    """Persist one completed SQL result as a SQLite view and return the final result."""
    view_name = orchestrator_view_name(run, sql_path=sql_output.sql_path)
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
            **_sql_output_result_fields(
                sql_output,
                saved_view_name=view_name,
                saved_view=saved_view,
            ),
            last_error=saved_view.get("message", f"Failed to save view {view_name}"),
            validation_feedback=validation_feedback,
            validation_attempts=validation_attempts,
            trace=append_stage_trace(run.trace, SAVE_STAGE, f"save_view: failed for view {view_name}"),
        )

    if validation_feedback:
        completion_reason = "validation_attempt_limit_saved_view"
        outcome = "failed"
        if not validation_feedback.get("retryable", True):
            completion_reason = "validation_blocked_saved_view"
            outcome = "blocked"
        return run.result(
            status="saved",
            outcome=outcome,
            completion_reason=completion_reason,
            **_sql_output_result_fields(
                sql_output,
                saved_view_name=view_name,
                saved_view=saved_view,
            ),
            last_error=validation_feedback["summary"],
            validation_feedback=validation_feedback,
            validation_attempts=validation_attempts,
            trace=append_stage_trace(run.trace, SAVE_STAGE, f"save_view: saved invalid result as view {view_name}"),
        )

    return run.result(
        status="saved",
        outcome="fulfilled",
        completion_reason="saved_view",
        **_sql_output_result_fields(
            sql_output,
            saved_view_name=view_name,
            saved_view=saved_view,
        ),
        last_error=None,
        validation_feedback=None,
        validation_attempts=validation_attempts,
        trace=append_stage_trace(run.trace, SAVE_STAGE, f"save_view: saved result as view {view_name}"),
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
            sql_path=None,
            last_error="SQL stage did not run.",
            validation_feedback=loop.validation_feedback,
            validation_attempts=loop.validation_attempts,
            trace=append_stage_trace(run.trace, SAVE_STAGE, "save_view: sql stage did not run"),
        )

    if sql_output.status != "complete":
        return run.result(
            status=sql_output.status,
            outcome="blocked" if sql_output.status == "blocked" else "failed",
            completion_reason=("sql_blocked" if sql_output.status == "blocked" else "sql_execution_failed"),
            **_sql_output_result_fields(sql_output),
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
        **_sql_output_result_fields(sql_output),
        last_error=last_error,
        validation_feedback=loop.validation_feedback,
        validation_attempts=loop.validation_attempts,
    )
