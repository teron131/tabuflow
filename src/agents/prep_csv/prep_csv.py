"""Tool-using prep_csv stage for iterative local data extraction."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain.messages import HumanMessage, ToolMessage
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import patch_config
from langgraph.graph.state import CompiledStateGraph

from ...tools.tabular import make_tabular_tools
from ..base import ApplicationAgent
from ..trace_utils import PREP_CSV_STAGE, append_stage_trace, append_trace
from ..prep_payloads import collect_extracted_sql_artifacts
from .prompts import PREP_CSV_STAGE_SYSTEM_PROMPT, build_prep_request, parse_tool_content
from .state import PrepCsvDecision, PrepCsvOutput

PREP_CSV_STAGE_RECURSION_LIMIT = 12


@dataclass
class PrepTrialResult:
    """One prep_csv trial result gathered from the tool-using agent loop."""

    decision: PrepCsvDecision | None
    extraction_results: list[dict[str, Any]]
    last_error: str | None
    trace: list[str]


@dataclass
class PrepTrialSummary:
    """Normalized readiness signals from one prep_csv trial."""

    extracted_sql_artifacts: list[dict[str, Any]]
    database_paths: set[str]
    trial_error: str | None
    decision_summary: str

    @property
    def extraction_ready(self) -> bool:
        """Return whether the trial produced one usable database and SQL artifacts."""
        return self.trial_error is None and len(self.database_paths) == 1 and bool(self.extracted_sql_artifacts)

    @property
    def database_path(self) -> str | None:
        """Return the single prepared database path when it is available."""
        if len(self.database_paths) != 1:
            return None
        return next(iter(self.database_paths))


def collect_prep_trial_result(result: dict[str, Any]) -> PrepTrialResult:
    """Collect structured decisions and tool artifacts from one prep_csv stage run."""
    trace: list[str] = []
    extraction_results: list[dict[str, Any]] = []
    last_error: str | None = None

    for message in result.get("messages", []):
        if not isinstance(message, ToolMessage):
            continue

        tool_name = message.name or "unknown_tool"
        tool_payload = parse_tool_content(message.content)
        tool_path = str(tool_payload.get("path", "")) if tool_payload else ""
        tool_label = f"{tool_name}({tool_path})" if tool_path else tool_name

        if message.status == "error":
            trace = append_trace(trace, f"tool failed: {tool_label}")
            last_error = str(message.content)
            continue

        if tool_name in {"inspect_tabular", "profile_tabular"}:
            trace = append_trace(trace, f"observed {tool_label}")
            continue

        if tool_name != "extract_tabular" or not tool_payload:
            continue

        extraction_results.append(tool_payload)
        if tool_payload.get("status") != "loaded":
            last_error = str(
                tool_payload.get(
                    "message",
                    f"Extraction failed for {tool_path or 'unknown file'}",
                )
            )
            continue
        trace = append_trace(trace, f"extracted {tool_label}")

    decision = None
    structured_response = result.get("structured_response")
    if structured_response is not None:
        decision = PrepCsvDecision.model_validate(structured_response)
        if decision.last_error:
            last_error = decision.last_error
        if decision.summary:
            trace = append_trace(trace, f"summary: {decision.summary}")

    return PrepTrialResult(
        decision=decision,
        extraction_results=extraction_results,
        last_error=last_error,
        trace=trace,
    )


def summarize_prep_trial(trial: PrepTrialResult) -> PrepTrialSummary:
    """Return the readiness summary for one prep_csv trial."""
    extracted_sql_artifacts = collect_extracted_sql_artifacts(trial.extraction_results)
    database_paths = {str(item.get("database_path")) for item in trial.extraction_results if item.get("database_path")}
    trial_error = trial.last_error
    if trial_error is None:
        if not trial.extraction_results:
            trial_error = "prep_csv stage finished without extracting any data."
        elif len(database_paths) != 1:
            trial_error = "Expected one shared SQLite database path after extraction."
        elif not extracted_sql_artifacts:
            trial_error = "prep_csv stage extracted data but did not produce usable SQL artifacts."

    decision = trial.decision
    decision_summary = decision.summary if decision and decision.summary else trial_error or "prep_csv trial finished without a usable extraction."
    return PrepTrialSummary(
        extracted_sql_artifacts=extracted_sql_artifacts,
        database_paths=database_paths,
        trial_error=trial_error,
        decision_summary=decision_summary,
    )


def _prepared_output(
    *,
    trial: PrepTrialResult,
    summary: PrepTrialSummary,
    prep_attempt: int,
    trace: list[str],
) -> PrepCsvOutput:
    """Build the successful prep_csv stage output."""
    database_path = summary.database_path or ""
    success_message = f"prepared {len(summary.extracted_sql_artifacts)} SQL artifact(s) into {database_path}"
    return PrepCsvOutput(
        status="prepared",
        database_path=database_path,
        extraction_results=trial.extraction_results,
        extracted_sql_artifacts=summary.extracted_sql_artifacts,
        prep_attempts=prep_attempt,
        trace=append_stage_trace(
            trace,
            PREP_CSV_STAGE,
            success_message,
        ),
    )


def _stopped_output(
    *,
    trial: PrepTrialResult,
    summary: PrepTrialSummary,
    prep_attempt: int,
    trace: list[str],
) -> PrepCsvOutput:
    """Build the prep_csv stage output for non-retryable stop decisions."""
    decision = trial.decision
    stop_message = f"stopped after trial {prep_attempt} with status={decision.status if decision else 'error'}"
    return PrepCsvOutput(
        status="error",
        extraction_results=trial.extraction_results,
        extracted_sql_artifacts=summary.extracted_sql_artifacts,
        last_error=(decision.last_error if decision else None) or summary.trial_error or (decision.summary if decision else None),
        prep_attempts=prep_attempt,
        trace=append_stage_trace(
            trace,
            PREP_CSV_STAGE,
            stop_message,
        ),
    )


def _exhausted_output(
    *,
    trial: PrepTrialResult,
    safe_max_prep_trials: int,
    trace: list[str],
) -> PrepCsvOutput:
    """Build the prep_csv stage output after all retry trials are exhausted."""
    final_decision = trial.decision
    final_error = (
        final_decision.last_error
        if final_decision and final_decision.last_error
        else trial.last_error or (final_decision.summary if final_decision else None) or f"prep_csv stage exhausted {safe_max_prep_trials} trial(s) without a usable extraction."
    )
    return PrepCsvOutput(
        status="error",
        extraction_results=trial.extraction_results,
        extracted_sql_artifacts=collect_extracted_sql_artifacts(trial.extraction_results),
        last_error=final_error,
        prep_attempts=safe_max_prep_trials,
        trace=append_stage_trace(trace, PREP_CSV_STAGE, f"exhausted {safe_max_prep_trials} trial(s)"),
    )


class PrepCsv(ApplicationAgent):
    """Use prep_csv tools iteratively to decide the final extraction shape."""

    def __init__(
        self,
        *,
        llm: BaseChatModel | None = None,
        prompt: str = "",
        root_dir: str | Path | None = None,
        state_schema: type[Any] | None = None,
        middleware: Sequence[Any] = (),
    ):
        super().__init__(llm=llm)
        self.prompt = prompt
        self.root_dir = root_dir
        self.state_schema = state_schema
        self.middleware = middleware
        self.graph = self.build_graph()

    def build_graph(
        self,
        *,
        state_schema: type[Any] | None = None,
        middleware: Sequence[Any] | None = None,
    ) -> CompiledStateGraph:
        """Build the compiled prep_csv stage graph."""
        return create_agent(
            model=self.llm,
            tools=make_tabular_tools(root_dir=self.root_dir),
            system_prompt=PREP_CSV_STAGE_SYSTEM_PROMPT,
            response_format=ToolStrategy(PrepCsvDecision),
            state_schema=state_schema or self.state_schema,
            middleware=self.middleware if middleware is None else middleware,
            name="prep_csv",
        )

    def run_trial(
        self,
        request: str,
        *,
        config: RunnableConfig | None = None,
    ) -> PrepTrialResult:
        """Run one prep_csv stage trial and collect the resulting tool outputs."""
        result = self.graph.invoke(
            {"messages": [HumanMessage(content=request)]},
            config=patch_config(config, recursion_limit=PREP_CSV_STAGE_RECURSION_LIMIT),
        )
        return collect_prep_trial_result(result)

    def invoke(
        self,
        message: str,
        *,
        source_files: list[str],
        worker_instructions: str = "",
        skill_refs: list[dict[str, Any]] | None = None,
        max_prep_trials: int = 2,
        config: RunnableConfig | None = None,
    ) -> PrepCsvOutput:
        """Run the prep_csv stage in bounded trials and normalize the final outputs."""
        safe_max_prep_trials = max(1, max_prep_trials)
        trace: list[str] = []
        previous_attempts: list[str] = []
        retry_instructions: list[str] = []
        last_trial: PrepTrialResult | None = None

        for prep_attempt in range(1, safe_max_prep_trials + 1):
            request = build_prep_request(
                self.prompt,
                message,
                source_files,
                prep_attempt=prep_attempt,
                max_prep_trials=safe_max_prep_trials,
                worker_instructions=worker_instructions,
                skill_refs=skill_refs or [],
                previous_attempts=previous_attempts,
                retry_instructions=retry_instructions,
            )
            trial = self.run_trial(request, config=config)
            last_trial = trial

            for message in trial.trace:
                trace = append_stage_trace(trace, PREP_CSV_STAGE, f"trial {prep_attempt} {message}")

            decision = trial.decision
            summary = summarize_prep_trial(trial)
            previous_attempts.append(f"trial {prep_attempt}: {summary.decision_summary}")

            if summary.extraction_ready:
                return _prepared_output(
                    trial=trial,
                    summary=summary,
                    prep_attempt=prep_attempt,
                    trace=trace,
                )

            if decision is not None and decision.status in {"blocked", "error"}:
                return _stopped_output(
                    trial=trial,
                    summary=summary,
                    prep_attempt=prep_attempt,
                    trace=trace,
                )

            retry_instructions = decision.retry_instructions if decision is not None else []
            if prep_attempt < safe_max_prep_trials:
                trace = append_stage_trace(
                    trace,
                    PREP_CSV_STAGE,
                    f"retrying after trial {prep_attempt}: {summary.decision_summary}",
                )

        if last_trial is None:
            return PrepCsvOutput(
                status="error",
                last_error="prep_csv stage did not run.",
                trace=append_stage_trace(trace, PREP_CSV_STAGE, "failed before starting"),
            )

        return _exhausted_output(
            trial=last_trial,
            safe_max_prep_trials=safe_max_prep_trials,
            trace=trace,
        )
