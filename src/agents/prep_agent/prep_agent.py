"""Tool-using prep agent for iterative local data extraction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.messages import HumanMessage, ToolMessage
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph

from ...clients.openai import ChatOpenAI
from ...tools.tabular import make_tabular_tools
from ..config import DEFAULT_REASONING_EFFORT, get_agent_settings
from .payloads import collect_extracted_targets
from .prompts import PREP_AGENT_SYSTEM_PROMPT, build_prep_request, parse_tool_content
from .state import PrepAgentDecision, PrepTaskInput, PrepTaskOutput, append_trace

PREP_AGENT_RECURSION_LIMIT = 12


@dataclass
class PrepTrialResult:
    """One prep-trial result gathered from the tool-using agent loop."""

    decision: PrepAgentDecision | None
    extraction_results: list[dict[str, Any]]
    last_error: str | None
    trace: list[str]


class PrepAgent:
    """Use prep tools iteratively to decide the final extraction shape."""

    def __init__(
        self,
        *,
        llm: BaseChatModel | None = None,
        prompt: str = "",
        root_dir: str | Path | None = None,
    ):
        self.prompt = prompt
        self.root_dir = root_dir
        if llm is None:
            resolved_model = get_agent_settings().resolve_worker_model()
            llm = ChatOpenAI(
                model=resolved_model,
                temperature=0,
                reasoning_effort=DEFAULT_REASONING_EFFORT,
            )
        self.llm = llm
        self.graph = self.build_graph()

    def build_graph(self) -> CompiledStateGraph:
        """Build the compiled prep-agent graph."""
        return create_agent(
            model=self.llm,
            tools=make_tabular_tools(root_dir=self.root_dir),
            system_prompt=PREP_AGENT_SYSTEM_PROMPT,
            response_format=PrepAgentDecision,
            name="prep_agent",
        )

    def _run_trial(self, request: str) -> PrepTrialResult:
        """Run one prep-agent trial and collect the resulting tool outputs."""
        result = self.graph.invoke(
            {"messages": [HumanMessage(content=request)]},
            config={"recursion_limit": PREP_AGENT_RECURSION_LIMIT},
        )

        trace: list[str] = []
        extraction_results: list[dict[str, Any]] = []
        last_error: str | None = None

        for message in result.get("messages", []):
            if isinstance(message, ToolMessage):
                tool_name = message.name or "unknown_tool"
                tool_payload = parse_tool_content(message.content)
                tool_path = str(tool_payload.get("path", "")) if tool_payload else ""
                tool_label = f"{tool_name}({tool_path})" if tool_path else tool_name
                if message.status == "error":
                    trace = append_trace(trace, f"prep tool failed: {tool_label}")
                    last_error = str(message.content)
                    continue

                if tool_name != "extract_tabular" or not tool_payload:
                    continue

                extraction_results.append(tool_payload)
                if tool_payload.get("status") != "loaded":
                    last_error = str(tool_payload.get("message", f"Extraction failed for {tool_path or 'unknown file'}"))
                    continue
                trace = append_trace(trace, f"prep extracted {tool_path or 'one source file'}")

        decision = None
        structured_response = result.get("structured_response")
        if structured_response is not None:
            decision = PrepAgentDecision.model_validate(structured_response)
            if decision.last_error:
                last_error = decision.last_error
            if decision.summary:
                trace = append_trace(trace, f"prep summary: {decision.summary}")

        return PrepTrialResult(
            decision=decision,
            extraction_results=extraction_results,
            last_error=last_error,
            trace=trace,
        )

    def invoke(
        self,
        task: str,
        *,
        source_files: list[str],
        worker_instructions: str = "",
        skill_refs: list[dict[str, Any]] | None = None,
        max_prep_trials: int = 2,
    ) -> PrepTaskOutput:
        """Run the prep agent in bounded trials and normalize the final outputs."""
        prep_input = PrepTaskInput(
            task=task,
            source_files=source_files,
            worker_instructions=worker_instructions,
            skill_refs=skill_refs or [],
            max_prep_trials=max_prep_trials,
        )
        safe_max_prep_trials = max(1, prep_input.max_prep_trials)
        trace: list[str] = []
        previous_attempts: list[str] = []
        retry_instructions: list[str] = []
        last_trial: PrepTrialResult | None = None

        for prep_attempt in range(1, safe_max_prep_trials + 1):
            request = build_prep_request(
                self.prompt,
                prep_input.task,
                prep_input.source_files,
                prep_attempt=prep_attempt,
                max_prep_trials=safe_max_prep_trials,
                worker_instructions=prep_input.worker_instructions,
                skill_refs=prep_input.skill_refs,
                previous_attempts=previous_attempts,
                retry_instructions=retry_instructions,
            )
            trial = self._run_trial(request)
            last_trial = trial

            for message in trial.trace:
                trace = append_trace(trace, f"[trial {prep_attempt}] {message}")

            decision = trial.decision
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
            decision_summary = decision.summary if decision and decision.summary else (trial_error or "Prep trial finished without a usable extraction.")
            previous_attempts.append(f"trial {prep_attempt}: {decision_summary}")

            if trial_error is None and len(database_paths) == 1 and extracted_targets and (decision is None or decision.status == "prepared"):
                database_path = next(iter(database_paths))
                return PrepTaskOutput(
                    status="prepared",
                    database_path=database_path,
                    extraction_results=trial.extraction_results,
                    extracted_targets=extracted_targets,
                    prep_attempts=prep_attempt,
                    trace=append_trace(trace, f"prep agent prepared {len(extracted_targets)} targets into {database_path}"),
                )

            if decision is not None and decision.status in {"blocked", "error"}:
                return PrepTaskOutput(
                    status="error",
                    extraction_results=trial.extraction_results,
                    extracted_targets=extracted_targets,
                    last_error=decision.last_error or trial_error or decision.summary,
                    prep_attempts=prep_attempt,
                    trace=append_trace(trace, f"prep agent stopped after trial {prep_attempt} with status={decision.status}"),
                )

            retry_instructions = decision.retry_instructions if decision is not None else []
            if prep_attempt < safe_max_prep_trials:
                trace = append_trace(trace, f"prep retrying after trial {prep_attempt}: {decision_summary}")

        if last_trial is None:
            return PrepTaskOutput(
                status="error",
                last_error="Prep agent did not run.",
                trace=append_trace(trace, "prep agent failed before starting"),
            )

        final_decision = last_trial.decision
        final_error = (
            final_decision.last_error
            if final_decision and final_decision.last_error
            else last_trial.last_error
            or (final_decision.summary if final_decision else None)
            or f"Prep agent exhausted {safe_max_prep_trials} trial(s) without a usable extraction."
        )
        return PrepTaskOutput(
            status="error",
            extraction_results=last_trial.extraction_results,
            extracted_targets=collect_extracted_targets(last_trial.extraction_results),
            last_error=final_error,
            prep_attempts=safe_max_prep_trials,
            trace=append_trace(trace, f"prep agent exhausted {safe_max_prep_trials} trial(s)"),
        )
