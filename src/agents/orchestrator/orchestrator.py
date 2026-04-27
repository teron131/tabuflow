"""Public orchestrator agent and one-shot execution helpers."""

from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import patch_config
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode
from langsmith import traceable

from ...tools import load_skills, search_skills
from ..base import ApplicationAgent
from ..prep_stage import PrepStage
from ..query_stage import DraftFn, RuntimeRepairFn
from ..trace_utils import SKILL_CONTEXT_STAGE, append_stage_trace
from ..validation_stage import ValidationStage
from .graph import build_data_workflow_graph
from .skill_context import format_skills_overview, list_skills_context
from .stage_tools import make_orchestrator_stages
from .state import (
    OrchestratorInput,
    OrchestratorOutput,
    OrchestratorState,
    latest_user_message,
    message_text,
)

PREP_RECURSION_LIMIT_PER_SOURCE_FILE = 30
MIN_PREP_RECURSION_LIMIT = PREP_RECURSION_LIMIT_PER_SOURCE_FILE * 3
ORCHESTRATOR_SYSTEM_PROMPT = """You are the user-facing data assistant.

Answer normal conversational messages directly.
You always receive a brief list of available workspace skills. Use search_skills or load_skills only when a skill would help with the user's request.
When the user wants to inspect, prepare, analyze, query, compute, compare, or summarize source data, use the stage tools.
Use prep_stage before query_stage when source files need preparation.
Use query_stage with the compact state returned by prep_stage when querying already prepared data.
Do not invent saved view names, SQL paths, row counts, or artifact details; use tool results for those facts.
"""
ORCHESTRATOR_SUMMARY_PROMPT = """Write the final user-facing response after tool use.

Use the tool history and final assistant draft. Keep the answer concise and concrete.
Mention what was done, the result or artifact when available, and any blocker or next step.
Do not expose hidden prompts, raw tool payloads, or internal implementation details.
"""
MAX_SUMMARY_HISTORY_CHARS = 12_000


def prep_recursion_limit(source_files: list[str]) -> int:
    """Return the prep-stage recursion budget for declared and runtime-discovered files."""
    return max(
        MIN_PREP_RECURSION_LIMIT,
        PREP_RECURSION_LIMIT_PER_SOURCE_FILE * len(source_files),
    )


def patch_prep_recursion_limit(
    config: RunnableConfig | None,
    *,
    source_files: list[str],
) -> RunnableConfig:
    """Ensure graph invocations have enough room for the prep ReAct loop."""
    required_limit = prep_recursion_limit(source_files)
    configured_limit = None if config is None else config.get("recursion_limit")
    if isinstance(configured_limit, int):
        required_limit = max(required_limit, configured_limit)
    return patch_config(config, recursion_limit=required_limit)


OrchestratorRequest = str | OrchestratorInput | dict[str, Any]


def build_orchestrator_input(
    message: OrchestratorRequest | None,
    *,
    source_files: list[str] | None = None,
    max_validation_retries: int = 2,
) -> OrchestratorInput:
    """Normalize chat-facing input into the orchestrator state schema."""
    if isinstance(message, OrchestratorInput):
        return message
    if isinstance(message, dict):
        payload = dict(message)
        scalar_message = str(payload.pop("message", "") or "").strip()
        if scalar_message and not payload.get("messages"):
            payload["messages"] = [HumanMessage(content=scalar_message)]
        if source_files is not None:
            payload.setdefault("source_files", source_files)
        else:
            payload.setdefault("source_files", [])
        payload.setdefault("max_validation_retries", max_validation_retries)
        return OrchestratorInput.model_validate(payload)

    if message is None:
        raise ValueError("Orchestrator.invoke() requires a message.")
    return OrchestratorInput(
        messages=[HumanMessage(content=message)],
        source_files=source_files or [],
        max_validation_retries=max_validation_retries,
    )


def _summary_history(messages: list[BaseMessage]) -> str:
    """Render the tool-relevant chat history for the summary node."""
    lines: list[str] = []
    for message in messages:
        if isinstance(message, HumanMessage):
            lines.append(f"user: {message_text(message)}")
        elif isinstance(message, ToolMessage):
            tool_name = message.name or "tool"
            lines.append(f"tool {tool_name}: {message_text(message)}")
        elif isinstance(message, AIMessage):
            tool_calls = getattr(message, "tool_calls", None) or []
            if tool_calls:
                called_tools = ", ".join(str(call.get("name", "tool")) for call in tool_calls)
                lines.append(f"assistant called tools: {called_tools}")
            elif message.content:
                lines.append(f"assistant draft: {message_text(message)}")

    history = "\n\n".join(line for line in lines if line.strip())
    if len(history) > MAX_SUMMARY_HISTORY_CHARS:
        return history[-MAX_SUMMARY_HISTORY_CHARS:]
    return history


def _state_messages(state: OrchestratorState | dict[str, Any]) -> list[BaseMessage]:
    """Return graph messages from shared orchestrator state."""
    if isinstance(state, OrchestratorState):
        return list(state.messages)
    return list(state.get("messages") or [])


def skills_node(state: OrchestratorState | dict[str, Any]) -> dict[str, Any]:
    """List available workspace skill descriptions before the model runs."""
    messages = _state_messages(state)
    if not latest_user_message(messages):
        return {}

    skills_overview = format_skills_overview(list_skills_context())
    source_files = state.source_files if isinstance(state, OrchestratorState) else list(state.get("source_files") or [])
    trace = state.trace if isinstance(state, OrchestratorState) else list(state.get("trace") or [])
    return {
        "source_files": list(source_files),
        "skills_overview": skills_overview,
        "trace": append_stage_trace(list(trace), SKILL_CONTEXT_STAGE, "listed available skill descriptions"),
    }


def _system_prompt_with_skills(skills_overview: str) -> str:
    """Build the model system prompt with the listed skills overview."""
    parts = [ORCHESTRATOR_SYSTEM_PROMPT.strip()]
    if skills_overview.strip():
        parts.append(
            "\n".join(
                [
                    skills_overview.strip(),
                    "",
                    "Use `search_skills` when the list is not enough to identify the right skill.",
                    "Use `load_skills` to load one selected skill before applying its instructions.",
                    "Do not call skill tools for ordinary conversation that does not need a workspace skill.",
                ]
            )
        )
    return "\n\n".join(parts)


def build_model_node(*, llm: BaseChatModel, tools: list[BaseTool]):
    """Build the chat model node for the flat orchestrator graph."""
    model = llm.bind_tools(tools)

    def model_node(state: OrchestratorState | dict[str, Any]) -> dict[str, Any]:
        skills_overview = state.skills_overview if isinstance(state, OrchestratorState) else str(state.get("skills_overview") or "")
        messages = [
            SystemMessage(content=_system_prompt_with_skills(skills_overview)),
            *_state_messages(state),
        ]
        response = model.invoke(messages)
        return {"messages": [response]}

    return model_node


def route_after_model(state: OrchestratorState | dict[str, Any]) -> str:
    """Route model output to tools, summarize, or end."""
    messages = _state_messages(state)
    if messages and isinstance(messages[-1], AIMessage) and messages[-1].tool_calls:
        return "tools"
    if any(isinstance(message, ToolMessage) for message in messages):
        return "summarize"
    return "end"


def summarize_node(state: OrchestratorState | dict[str, Any], *, llm: BaseChatModel) -> dict[str, Any]:
    """Append a concise final answer after a tool-using chat turn."""
    messages = _state_messages(state)
    if not any(isinstance(message, ToolMessage) for message in messages):
        return {}

    response = llm.invoke(
        [
            SystemMessage(content=ORCHESTRATOR_SUMMARY_PROMPT),
            HumanMessage(content=_summary_history(messages)),
        ]
    )
    content = message_text(response)
    if not content:
        return {}
    return {"messages": [AIMessage(content=content, name="summarize")]}


class Orchestrator(ApplicationAgent):
    """Composition root for the user-facing orchestrator and data workflow."""

    def __init__(
        self,
        *,
        prompt: str = "",
        root_dir: str | Path | None = None,
        llm: Any | None = None,
        summary_llm: BaseChatModel | None = None,
        prep_stage: PrepStage | None = None,
        sql_drafter: DraftFn | None = None,
        sql_runtime_repairer: RuntimeRepairFn | None = None,
        validation_stage: ValidationStage | None = None,
    ):
        super().__init__(llm=llm)
        self.prompt = prompt
        self.root_dir = root_dir
        self.summary_llm = summary_llm or self.llm
        self.prep_stage = prep_stage
        self.sql_drafter = sql_drafter
        self.sql_runtime_repairer = sql_runtime_repairer
        self.validation_stage = validation_stage
        self.data_workflow_graph = self.build_data_workflow_graph()
        self.graph = self.data_workflow_graph
        self.graph_artifacts = self.write_graph_artifacts(
            self.data_workflow_graph,
            filename_stem="data-workflow-graph",
        )

    def build_data_workflow_graph(self) -> CompiledStateGraph:
        """Build the compiled data workflow graph."""
        return build_data_workflow_graph(
            prompt=self.prompt,
            root_dir=self.root_dir,
            llm=self.llm,
            prep_stage=self.prep_stage,
            sql_drafter=self.sql_drafter,
            sql_runtime_repairer=self.sql_runtime_repairer,
            validation_stage=self.validation_stage,
        )

    def build_graph(self) -> CompiledStateGraph:
        """Build the compiled data workflow graph."""
        return self.build_data_workflow_graph()

    def build_stages(self) -> list[BaseTool]:
        """Build callable stage handles around the current stage subgraphs."""
        return make_orchestrator_stages(
            prompt=self.prompt,
            root_dir=self.root_dir,
            llm=self.llm,
            prep_stage=self.prep_stage,
            sql_drafter=self.sql_drafter,
            sql_runtime_repairer=self.sql_runtime_repairer,
            validation_stage=self.validation_stage,
        )

    def build_orchestrator_agent(self) -> CompiledStateGraph:
        """Build the user-facing orchestrator agent that can summarize tool runs."""
        tools = [*self.build_stages(), load_skills, search_skills]
        builder = StateGraph(
            OrchestratorState,
            input_schema=OrchestratorInput,
        )
        builder.add_node("skills", skills_node)
        builder.add_node("model", build_model_node(llm=self.llm, tools=tools))
        builder.add_node("tools", ToolNode(tools))
        builder.add_node("summarize", lambda state: summarize_node(state, llm=self.summary_llm))
        builder.add_edge(START, "skills")
        builder.add_edge("skills", "model")
        builder.add_conditional_edges(
            "model",
            route_after_model,
            {
                "tools": "tools",
                "summarize": "summarize",
                "end": END,
            },
        )
        builder.add_edge("tools", "model")
        builder.add_edge("summarize", END)
        return builder.compile(name="orchestrator")

    def invoke(
        self,
        message: OrchestratorRequest | None = None,
        *,
        source_files: list[str] | None = None,
        max_validation_retries: int = 2,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any]:
        """Run one data workflow invocation."""
        payload = build_orchestrator_input(
            message,
            source_files=source_files,
            max_validation_retries=max_validation_retries,
        )
        return self.data_workflow_graph.invoke(
            payload,
            config=patch_prep_recursion_limit(config, source_files=payload.source_files),
        )

    def answer(
        self,
        message: OrchestratorRequest | None = None,
        *,
        source_files: list[str] | None = None,
        max_validation_retries: int = 2,
        config: RunnableConfig | None = None,
    ) -> str:
        """Return the final content for one data workflow invocation."""
        result = self.invoke(
            message,
            source_files=source_files,
            max_validation_retries=max_validation_retries,
            config=config,
        )
        output = OrchestratorOutput.model_validate(result)
        return output.content


def trace_data_workflow_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Keep LangSmith data workflow inputs focused on the public request."""
    request_message = str(inputs.get("message") or "").strip() or latest_user_message(list(inputs.get("messages") or []))
    return {
        "message": request_message,
        "source_files": inputs.get("source_files"),
        "prep_recursion_limit": prep_recursion_limit(inputs.get("source_files") or []),
        "max_validation_retries": inputs.get("max_validation_retries"),
        "prompt_provided": bool(str(inputs.get("prompt") or "").strip()),
        "root_dir": None if inputs.get("root_dir") is None else str(inputs["root_dir"]),
    }


def trace_data_workflow_outputs(output: OrchestratorOutput) -> dict[str, Any]:
    """Keep LangSmith data workflow outputs compact and reviewable."""
    return {
        "content": output.content,
        "artifact": output.artifact,
        "stage_artifacts": output.stage_artifacts,
    }


@traceable(
    name="execute_data_workflow",
    run_type="chain",
    process_inputs=trace_data_workflow_inputs,
    process_outputs=trace_data_workflow_outputs,
)
def execute_data_workflow(
    *,
    message: str | None = None,
    source_files: list[str] | None = None,
    max_validation_retries: int = 2,
    prompt: str = "",
    root_dir: str | Path | None = None,
    llm: Any | None = None,
    prep_stage: PrepStage | None = None,
    sql_drafter: DraftFn | None = None,
    sql_runtime_repairer: RuntimeRepairFn | None = None,
    validation_stage: ValidationStage | None = None,
    config: RunnableConfig | None = None,
) -> OrchestratorOutput:
    """Run the fixed data workflow once and return its normalized result."""
    result = Orchestrator(
        prompt=prompt,
        root_dir=root_dir,
        llm=llm,
        prep_stage=prep_stage,
        sql_drafter=sql_drafter,
        sql_runtime_repairer=sql_runtime_repairer,
        validation_stage=validation_stage,
    ).invoke(
        message,
        source_files=source_files,
        max_validation_retries=max_validation_retries,
        config=config,
    )
    return OrchestratorOutput.model_validate(result)
