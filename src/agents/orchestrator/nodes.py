"""Nodes, routes, and middleware for the orchestrator graph."""

from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..prep_stage import PrepStage, PrepStageOutput
from ..prep_stage.payloads import collect_extracted_targets
from ..prep_stage.prep_stage import collect_prep_trial_result
from ..prep_stage.state import PrepStageDecision
from ..query_stage import DraftFn, RuntimeRepairFn, build_sql_drafter, build_sql_runtime_repairer
from ..query_stage.nodes import execute_node, make_repair_sql_node, make_write_node
from ..trace_utils import (
    PREP_STAGE,
    SKILL_CONTEXT_STAGE,
    VALIDATION_STAGE,
    append_stage_trace,
    append_trace_messages,
)
from ..validation_stage import ValidationStage
from .prompts import build_prep_stage_message, build_sql_worker_context, build_user_request_message
from .runtime import (
    PREP_STAGE_NAME,
    QUERY_STAGE_NAME,
    VALIDATION_STAGE_NAME,
    build_sql_failure_result,
    orchestrator_run_from_state,
    preferred_sql_targets,
    reset_sql_attempt_update,
    save_sql_result,
    sql_loop_from_state,
    sql_output_from_state,
)
from .skill_context import build_worker_skill_payload
from .state import OrchestratorState, SQLArtifactState, latest_user_message


def stage_report_message(name: str, content: str) -> AIMessage:
    """Build a compact stage report for the shared conversation spine."""
    return AIMessage(content=content, name=name)


def build_skill_context_update(
    prompt: str,
    *,
    message: str,
    source_files: list[str],
    trace: list[str],
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Build the deterministic skill-context update for stage tools."""
    skill_payload = build_worker_skill_payload(
        message,
        config=config,
    )
    return {
        "skill_refs": skill_payload.skill_refs,
        "messages": [
            build_user_request_message(
                message=message,
                source_files=source_files,
            ),
            build_prep_stage_message(
                prompt,
                message=message,
                source_files=source_files,
                worker_instructions=skill_payload.worker_instructions,
                skill_refs=skill_payload.skill_refs,
            ),
        ],
        "worker_context": build_sql_worker_context(
            prompt,
            worker_instructions=skill_payload.worker_instructions,
            skill_refs=skill_payload.skill_refs,
        ),
        "trace": append_stage_trace(trace, SKILL_CONTEXT_STAGE, f"loaded {len(skill_payload.skill_refs)} skill ref(s)"),
    }


class PrepResultMiddleware(
    AgentMiddleware[
        OrchestratorState,
        None,
        PrepStageDecision,
    ]
):
    """Collect the prep ReAct graph output into shared orchestrator fields."""

    state_schema = OrchestratorState

    def after_agent(
        self,
        state: OrchestratorState | dict[str, Any],
        runtime: Any,
    ) -> dict[str, Any]:
        """Store prep artifacts after the create_agent graph reaches its end."""
        _ = runtime
        state = normalize_orchestrator_state(state)
        prep_output = build_prep_stage_output(state)
        prep_artifact = prep_output.model_dump(mode="json")
        stage_artifacts = {
            **state.stage_artifacts,
            PREP_STAGE_NAME: prep_artifact,
        }
        return {
            "prep_output": prep_artifact,
            "stage_artifacts": stage_artifacts,
            "database_path": prep_output.database_path,
            "extracted_targets": prep_output.extracted_targets,
            "preferred_targets": preferred_sql_targets(prep_output.extracted_targets),
            "trace": prep_output.trace,
        }


def build_prep_stage_output(state: OrchestratorState | dict[str, Any]) -> PrepStageOutput:
    """Collect the visible prep ReAct graph result into orchestrator state."""
    state = normalize_orchestrator_state(state)

    trial = collect_prep_trial_result(
        {
            "messages": state.messages,
            "structured_response": state.structured_response,
        }
    )
    trace = state.trace
    for message in trial.trace:
        trace = append_stage_trace(trace, PREP_STAGE, message)

    extracted_targets = collect_extracted_targets(trial.extraction_results)
    database_paths = {str(item.get("database_path")) for item in trial.extraction_results if item.get("database_path")}
    trial_error = trial.last_error
    if trial_error is None:
        if not trial.extraction_results:
            trial_error = "Prep agent finished without extracting any data."
        elif len(database_paths) != 1:
            trial_error = "Expected one shared SQLite database path after extraction."
        elif not extracted_targets:
            trial_error = "Prep agent extracted data but did not produce usable targets."

    decision = trial.decision
    extraction_ready = trial_error is None and len(database_paths) == 1 and bool(extracted_targets)
    if extraction_ready:
        database_path = next(iter(database_paths))
        return PrepStageOutput(
            status="prepared",
            database_path=database_path,
            extraction_results=trial.extraction_results,
            extracted_targets=extracted_targets,
            prep_attempts=1,
            trace=append_stage_trace(
                trace,
                PREP_STAGE,
                f"prepared {len(extracted_targets)} target(s) into {database_path}",
            ),
        )

    last_error = trial_error
    if decision is not None:
        last_error = decision.last_error or last_error or decision.summary
    return PrepStageOutput(
        status="error",
        extraction_results=trial.extraction_results,
        extracted_targets=extracted_targets,
        last_error=last_error or "Prep agent did not produce a usable extraction.",
        prep_attempts=1,
        trace=append_stage_trace(trace, PREP_STAGE, "ended without a usable extraction"),
    )


def normalize_orchestrator_state(state: OrchestratorState | dict[str, Any]) -> OrchestratorState:
    """Normalize LangGraph dict state into the orchestrator state model."""
    if isinstance(state, OrchestratorState):
        return state
    return OrchestratorState.model_validate(state)


def workflow_trace_from_state(
    state: OrchestratorState,
    *,
    sql_output: SQLArtifactState | None = None,
) -> list[str]:
    """Merge parent and worker traces into one caller-facing workflow log."""
    trace: list[str] = []
    if state.prep_output is None:
        trace = append_trace_messages(trace, state.trace)
    else:
        prep_output = PrepStageOutput.model_validate(state.prep_output)
        trace = append_trace_messages(trace, prep_output.trace)

    if sql_output is None:
        sql_output = sql_output_from_state(state)
    if sql_output is not None:
        trace = append_trace_messages(trace, sql_output.trace)
    return trace


class OrchestratorNodes:
    """Stage node factory bound to one set of worker agents."""

    def __init__(
        self,
        *,
        prompt: str,
        root_dir: str | Path | None,
        llm: Any | None,
        prep_stage: PrepStage | None,
        sql_drafter: DraftFn | None,
        sql_runtime_repairer: RuntimeRepairFn | None,
        validation_stage: ValidationStage | None,
    ):
        self.prompt = prompt
        self.root_dir = root_dir
        self.llm = llm
        self._prep_stage = prep_stage
        if llm is None and (sql_drafter is None or sql_runtime_repairer is None):
            raise ValueError("SQL stage model functions require the orchestrator's shared llm.")
        self.sql_drafter = sql_drafter or build_sql_drafter(llm)
        self.sql_runtime_repairer = sql_runtime_repairer or build_sql_runtime_repairer(llm)
        self.sql_write_node = make_write_node(self.sql_drafter)
        self.repair_sql_node = make_repair_sql_node(self.sql_runtime_repairer)
        self.validation_stage = validation_stage or (ValidationStage(llm=llm) if llm is not None else ValidationStage())

    @property
    def prep_stage(self) -> PrepStage:
        """Return the prep stage, building it only when the prep stage is used."""
        if self._prep_stage is None:
            self._prep_stage = PrepStage(
                llm=self.llm,
                prompt=self.prompt,
                root_dir=self.root_dir,
            )
        return self._prep_stage

    def skill_context(
        self,
        state: OrchestratorState,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """Load message-relevant skill context into the parent orchestrator state."""
        message = latest_user_message(state.messages)
        return build_skill_context_update(
            self.prompt,
            message=message,
            source_files=state.source_files,
            trace=state.trace,
            config=config,
        )

    def prep_stage_graph(self) -> CompiledStateGraph:
        """Build the prep stage as the visible prep ReAct graph."""
        return self.prep_stage.build_graph(
            state_schema=OrchestratorState,
            middleware=[PrepResultMiddleware()],
        )

    def write_sql(self, state: OrchestratorState) -> dict[str, Any]:
        """Write the SQL artifact directly from shared orchestrator state."""
        update = self.sql_write_node(state)
        if update.get("status") == "written":
            content = f"Wrote SQL artifact {update.get('sql_path')} for targets: {', '.join(update.get('selected_targets', [])) or 'none'}."
        else:
            content = f"SQL write ended with status={update.get('status', state.status)}: {update.get('last_error') or 'No SQL artifact was written.'}"
        return {
            **update,
            "messages": [stage_report_message(QUERY_STAGE_NAME, content)],
        }

    def execute_sql(self, state: OrchestratorState) -> dict[str, Any]:
        """Execute the current SQL artifact directly from the orchestrator graph."""
        update = execute_node(state)
        if update.get("status") == "complete":
            row_count = (update.get("result") or {}).get("row_count")
            content = f"Executed SQL successfully on attempt {update.get('attempts')}; row_count={row_count}."
        elif update.get("status") == "needs_repair":
            content = f"SQL execution needs runtime repair after attempt {update.get('attempts')}: {update.get('last_error')}"
        else:
            content = f"SQL execution ended with status={update.get('status', state.status)}: {update.get('last_error') or 'No executable SQL result.'}"
        return {
            **update,
            "messages": [stage_report_message(QUERY_STAGE_NAME, content)],
        }

    def repair_sql(self, state: OrchestratorState) -> dict[str, Any]:
        """Repair SQLite runtime errors by editing the current SQL artifact."""
        update = self.repair_sql_node(state)
        if update.get("status") == "repaired":
            content = f"SQL repair pass {update.get('repair_count')} edited SQL artifact {update.get('sql_path')}."
        else:
            content = f"SQL repair ended with status={update.get('status', state.status)}: {update.get('last_error') or 'No SQL repair was applied.'}"
        return {
            **update,
            "messages": [stage_report_message(QUERY_STAGE_NAME, content)],
        }

    def query_stage_graph(self, *, name: str = "query_stage") -> CompiledStateGraph:
        """Build the SQL-write, execution, repair, and validation loop."""
        builder = StateGraph(OrchestratorState)
        builder.add_node("write_sql", self.write_sql)
        builder.add_node("execute_sql", self.execute_sql)
        builder.add_node("repair_sql", self.repair_sql)
        builder.add_node("validate", self.validate)
        builder.add_node("save_view", self.save_view)

        builder.add_edge(START, "write_sql")
        builder.add_edge("write_sql", "execute_sql")
        builder.add_conditional_edges(
            "execute_sql",
            route_after_execute_sql,
            {
                "validate": "validate",
                "repair_sql": "repair_sql",
            },
        )
        builder.add_edge("repair_sql", "execute_sql")
        builder.add_conditional_edges(
            "validate",
            route_after_validate,
            {
                "write_sql": "write_sql",
                "save_view": "save_view",
            },
        )
        builder.add_edge("save_view", END)
        return builder.compile(name=name)

    def validate(
        self,
        state: OrchestratorState,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """Validate the SQL result before saving a view."""
        sql_output = sql_output_from_state(state)
        if sql_output is None:
            return {
                "validation_feedback": {
                    "retryable": False,
                    "summary": "SQL stage did not run.",
                    "instructions": ["Run the SQL stage before validation."],
                },
                "trace": append_stage_trace(state.trace, VALIDATION_STAGE, "validate: could not find a SQL output"),
                "messages": [stage_report_message(VALIDATION_STAGE_NAME, "Validation could not find a SQL output.")],
            }
        sql_artifact = sql_output.model_dump(mode="json")
        validation_output = self.validation_stage.invoke(
            message=latest_user_message(state.messages),
            source_files=state.source_files,
            extracted_targets=state.extracted_targets,
            selected_targets=sql_output.selected_targets,
            candidate_sql=sql_output.candidate_sql,
            sql_result=sql_output.result,
            previous_feedback=state.validation_feedback,
            validation_attempts=state.validation_attempts,
            config=config,
        )
        validation_artifact = validation_output.model_dump(mode="json")
        workflow_trace = workflow_trace_from_state(state, sql_output=sql_output)
        stage_artifacts = {
            **state.stage_artifacts,
            QUERY_STAGE_NAME: sql_artifact,
            VALIDATION_STAGE_NAME: validation_artifact,
        }
        if validation_output.valid:
            return {
                "stage_artifacts": stage_artifacts,
                "validation_feedback": None,
                "trace": append_stage_trace(workflow_trace, VALIDATION_STAGE, "validate: accepted the SQL result"),
                "messages": [stage_report_message(VALIDATION_STAGE_NAME, validation_output.summary.strip() or "Validation accepted the SQL result.")],
            }

        summary = validation_output.summary.strip() or "The SQL result does not appear to fully satisfy the user message."
        instructions = [instruction.strip() for instruction in validation_output.instructions if instruction.strip()]
        validation_feedback = {
            "retryable": validation_output.retryable,
            "summary": summary,
            "instructions": instructions or [summary],
        }
        next_validation_attempts = state.validation_attempts + 1
        retry_update = reset_sql_attempt_update() if validation_output.retryable and next_validation_attempts <= state.max_validation_retries else {}
        return {
            **retry_update,
            "stage_artifacts": stage_artifacts,
            "validation_feedback": validation_feedback,
            "validation_attempts": next_validation_attempts,
            "trace": append_stage_trace(workflow_trace, VALIDATION_STAGE, f"validate: requested another SQL attempt: {validation_feedback['summary']}"),
            "messages": [stage_report_message(VALIDATION_STAGE_NAME, f"Validation requested another SQL attempt: {validation_feedback['summary']}")],
        }

    def save_view(self, state: OrchestratorState) -> dict[str, Any]:
        """Persist a completed SQL result before the answer node returns it."""
        sql_output = sql_output_from_state(state)
        if sql_output is None or sql_output.status != "complete":
            content, artifact = build_sql_failure_result(
                orchestrator_run_from_state(state),
                loop=sql_loop_from_state(state),
            )
        else:
            content, artifact = save_sql_result(
                orchestrator_run_from_state(state),
                sql_output=sql_output,
                validation_feedback=state.validation_feedback,
                validation_attempts=state.validation_attempts,
            )
        return {
            "content": content,
            "artifact": artifact,
            "stage_artifacts": state.stage_artifacts,
            "messages": [
                stage_report_message(
                    "save_view",
                    f"Save view completed with status={artifact.get('status')} and completion_reason={artifact.get('completion_reason')}.",
                )
            ],
        }


def route_after_execute_sql(state: OrchestratorState) -> str:
    """Route SQL execution through repair or validation."""
    sql_output = sql_output_from_state(state)
    if sql_output is not None and sql_output.status == "complete":
        return "validate"
    if state.status == "needs_repair" and state.repair_count < state.max_repairs:
        return "repair_sql"
    return "validate"


def route_after_validate(state: OrchestratorState) -> str:
    """Route validation retry, save-view persistence, or end the query-stage loop."""
    if state.validation_feedback is None:
        return "save_view"
    if state.validation_feedback.get("retryable", True) and state.validation_attempts <= state.max_validation_retries:
        return "write_sql"
    return "save_view"
