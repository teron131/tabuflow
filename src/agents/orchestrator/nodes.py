"""Nodes, routes, and middleware for the orchestrator graph."""

from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import patch_config
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..prep_agent import PrepAgent, PrepTaskOutput
from ..prep_agent.payloads import collect_extracted_targets
from ..prep_agent.prep_agent import collect_prep_trial_result
from ..prep_agent.state import PrepAgentDecision
from ..trace_utils import (
    PREP_STAGE,
    SKILL_CONTEXT_STAGE,
    VALIDATION_STAGE,
    append_stage_trace,
    append_trace_messages,
)
from ..validation_agent import ValidationAgent
from .prompts import build_prep_stage_message, build_sql_worker_context
from .runtime import (
    PREP_AGENT_NAME,
    SQL_STAGE_NAME,
    VALIDATION_AGENT_NAME,
    build_sql_failure_result,
    orchestrator_run_from_state,
    preferred_sql_targets,
    reset_sql_attempt_update,
    save_validated_result,
    sql_loop_from_state,
    sql_output_from_state,
)
from .skill_context import build_worker_skill_payload
from .sql_stage import DraftFn, RuntimeRepairFn, SQLStageOutput, build_sql_drafter, build_sql_runtime_repairer
from .sql_stage.nodes import execute_node, make_runtime_repair_node, make_write_node
from .state import OrchestratorState


class PrepResultMiddleware(AgentMiddleware[OrchestratorState, None, PrepAgentDecision]):
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
        prep_output = build_prep_task_output(state)
        prep_artifact = prep_output.model_dump(mode="json")
        agent_artifacts = {
            **state.agent_artifacts,
            PREP_AGENT_NAME: prep_artifact,
        }
        return {
            "prep_output": prep_artifact,
            "agent_artifacts": agent_artifacts,
            "database_path": prep_output.database_path,
            "extracted_targets": prep_output.extracted_targets,
            "preferred_targets": preferred_sql_targets(prep_output.extracted_targets),
            "trace": prep_output.trace,
            "active_agent": PREP_AGENT_NAME,
        }


def build_prep_task_output(state: OrchestratorState | dict[str, Any]) -> PrepTaskOutput:
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
        return PrepTaskOutput(
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
    return PrepTaskOutput(
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
    sql_output: SQLStageOutput | None = None,
) -> list[str]:
    """Merge parent and worker traces into one caller-facing workflow log."""
    trace: list[str] = []
    if state.prep_output is None:
        trace = append_trace_messages(trace, state.trace)
    else:
        prep_output = PrepTaskOutput.model_validate(state.prep_output)
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
        prep_agent: PrepAgent | None,
        sql_drafter: DraftFn | None,
        sql_runtime_repairer: RuntimeRepairFn | None,
        validation_agent: ValidationAgent | None,
    ):
        self.prompt = prompt
        self.root_dir = root_dir
        self.llm = llm
        self._prep_agent = prep_agent
        if llm is None and (sql_drafter is None or sql_runtime_repairer is None):
            raise ValueError("SQL stage model functions require the orchestrator's shared llm.")
        self.sql_drafter = sql_drafter or build_sql_drafter(llm)
        self.sql_runtime_repairer = sql_runtime_repairer or build_sql_runtime_repairer(llm)
        self.sql_write_node = make_write_node(self.sql_drafter)
        self.sql_runtime_repair_node = make_runtime_repair_node(self.sql_runtime_repairer)
        self.validation_agent = validation_agent or (ValidationAgent(llm=llm) if llm is not None else ValidationAgent())

    @property
    def prep_agent(self) -> PrepAgent:
        """Return the prep agent, building it only when the prep stage is used."""
        if self._prep_agent is None:
            self._prep_agent = PrepAgent(
                llm=self.llm,
                prompt=self.prompt,
                root_dir=self.root_dir,
            )
        return self._prep_agent

    def skill_context(
        self,
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
            "messages": [
                build_prep_stage_message(
                    self.prompt,
                    task=state.task,
                    source_files=state.source_files,
                    worker_instructions=skill_payload.worker_instructions,
                    skill_refs=skill_payload.skill_refs,
                )
            ],
            "worker_context": build_sql_worker_context(
                self.prompt,
                worker_instructions=skill_payload.worker_instructions,
                skill_refs=skill_payload.skill_refs,
            ),
            "trace": append_stage_trace(state.trace, SKILL_CONTEXT_STAGE, f"loaded {len(skill_payload.skill_refs)} skill ref(s)"),
        }

    def prep_stage_graph(self) -> CompiledStateGraph:
        """Build the prep stage as the visible prep ReAct graph."""
        return self.prep_agent.build_graph(
            state_schema=OrchestratorState,
            middleware=[PrepResultMiddleware()],
        )

    def write_sql(self, state: OrchestratorState) -> dict[str, Any]:
        """Write the SQL artifact directly from shared orchestrator state."""
        return {
            **self.sql_write_node(state),
            "active_agent": SQL_STAGE_NAME,
        }

    def execute_sql(self, state: OrchestratorState) -> dict[str, Any]:
        """Execute the current SQL artifact directly from the orchestrator graph."""
        return {
            **execute_node(state),
            "active_agent": SQL_STAGE_NAME,
        }

    def runtime_repair_sql(self, state: OrchestratorState) -> dict[str, Any]:
        """Repair SQLite runtime errors by editing the current SQL artifact."""
        return {
            **self.sql_runtime_repair_node(state),
            "active_agent": SQL_STAGE_NAME,
        }

    def query_stage_graph(self, *, name: str = "query_stage") -> CompiledStateGraph:
        """Build the SQL-write, execution, repair, and validation loop."""
        builder = StateGraph(OrchestratorState)
        builder.add_node("write_sql", self.write_sql)
        builder.add_node("execute_sql", self.execute_sql)
        builder.add_node("runtime_repair", self.runtime_repair_sql)
        builder.add_node("validate", self.validate)

        builder.add_edge(START, "write_sql")
        builder.add_edge("write_sql", "execute_sql")
        builder.add_conditional_edges(
            "execute_sql",
            route_after_execute_sql,
            {
                "validate": "validate",
                "runtime_repair": "runtime_repair",
            },
        )
        builder.add_edge("runtime_repair", "execute_sql")
        builder.add_conditional_edges(
            "validate",
            route_after_validate,
            {
                "write_sql": "write_sql",
                END: END,
            },
        )
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
                "trace": append_stage_trace(state.trace, VALIDATION_STAGE, "could not find a SQL output"),
                "active_agent": VALIDATION_AGENT_NAME,
            }
        sql_artifact = sql_output.model_dump(mode="json")
        validation_output = self.validation_agent.invoke(
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
        workflow_trace = workflow_trace_from_state(state, sql_output=sql_output)
        agent_artifacts = {
            **state.agent_artifacts,
            SQL_STAGE_NAME: sql_artifact,
            VALIDATION_AGENT_NAME: validation_artifact,
        }
        if validation_output.valid:
            return {
                "sql_output": sql_artifact,
                "agent_artifacts": agent_artifacts,
                "validation_feedback": None,
                "trace": append_stage_trace(workflow_trace, VALIDATION_STAGE, "accepted the SQL result"),
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
            **reset_sql_attempt_update(),
            "sql_output": sql_artifact,
            "agent_artifacts": agent_artifacts,
            "validation_feedback": validation_feedback,
            "validation_attempts": state.validation_attempts + 1,
            "trace": append_stage_trace(workflow_trace, VALIDATION_STAGE, f"requested another SQL attempt: {validation_feedback['summary']}"),
            "active_agent": VALIDATION_AGENT_NAME,
        }

    def save(self, state: OrchestratorState) -> dict[str, Any]:
        """Persist a validated SQL result as the terminal orchestrator artifact."""
        if state.sql_output is None:
            content, artifact = build_sql_failure_result(
                orchestrator_run_from_state(state),
                loop=sql_loop_from_state(state),
            )
        else:
            content, artifact = save_validated_result(
                orchestrator_run_from_state(state),
                sql_output=SQLStageOutput.model_validate(state.sql_output),
                validation_attempts=state.validation_attempts,
            )
        return {
            "content": content,
            "artifact": artifact,
            "agent_artifacts": state.agent_artifacts,
            "active_agent": None,
        }

    def finalize(self, state: OrchestratorState) -> dict[str, Any]:
        """Build the terminal orchestrator artifact for blocked or failed runs."""
        if state.content and state.artifact:
            return {
                "content": state.content,
                "artifact": state.artifact,
                "agent_artifacts": state.agent_artifacts,
                "active_agent": state.active_agent,
            }

        run = orchestrator_run_from_state(state)
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
                sql_path=None,
                last_error=(None if prep_output is None else prep_output.last_error) or "Preparation failed.",
                validation_feedback=None,
                validation_attempts=0,
            )
            return {
                "content": content,
                "artifact": artifact,
                "agent_artifacts": state.agent_artifacts,
                "active_agent": PREP_AGENT_NAME,
            }

        content, artifact = build_sql_failure_result(
            run,
            loop=sql_loop_from_state(state),
        )
        return {
            "content": content,
            "artifact": artifact,
            "agent_artifacts": state.agent_artifacts,
            "active_agent": (VALIDATION_AGENT_NAME if state.validation_feedback is not None else SQL_STAGE_NAME),
        }


def route_after_prep_stage(state: OrchestratorState) -> str:
    """Route to SQL only after prep produces a usable database target."""
    prep_output = None if state.prep_output is None else PrepTaskOutput.model_validate(state.prep_output)
    return "query_stage" if prep_output is not None and prep_output.status == "prepared" else "finalize"


def route_after_execute_sql(state: OrchestratorState) -> str:
    """Route SQL execution through repair or validation."""
    sql_output = sql_output_from_state(state)
    if sql_output is not None and sql_output.status == "complete":
        return "validate"
    if state.status == "needs_repair" and state.repair_count < state.max_repairs:
        return "runtime_repair"
    return "validate"


def route_after_validate(state: OrchestratorState) -> str:
    """Route validation retry or end the query-stage loop."""
    if state.validation_feedback is None:
        return END
    if not state.validation_feedback.get("retryable", True):
        return END
    if state.validation_attempts <= state.max_validation_retries:
        return "write_sql"
    return END


def route_after_query_stage(state: OrchestratorState) -> str:
    """Route a completed query stage to save or terminal finalization."""
    sql_output = sql_output_from_state(state)
    if sql_output is not None and sql_output.status == "complete" and state.validation_feedback is None:
        return "save"
    return "finalize"
