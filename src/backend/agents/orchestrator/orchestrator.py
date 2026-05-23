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

from tabuflow import list_skills
from tabuflow.fs import allow_sql_or_skill_write
from tabuflow.fs.hashline import HashlineReferenceError
from ..base import ApplicationAgent
from ..prep_csv import PrepCsv
from ..prep_pdf import PrepPdf
from ..query_stage import SQLRepairerFn, SQLWriterFn
from ..tool_adapter import make_fs_tools, make_skill_tools
from ..trace_utils import SKILL_CONTEXT_STAGE, append_stage_trace
from ..validation_stage import ValidationStage
from .skill_context import SKILLS_PATH, format_skills_overview
from .stage_tools import make_orchestrator_stages
from .state import (
    OrchestratorInput,
    OrchestratorState,
    latest_user_message,
    message_text,
)

PREP_RECURSION_LIMIT_PER_SOURCE_FILE = 30
MIN_PREP_RECURSION_LIMIT = PREP_RECURSION_LIMIT_PER_SOURCE_FILE * 3
ORCHESTRATOR_SYSTEM_PROMPT = """You are the user-facing assistant inside Tabuflow Workbench.

Role:
- Help the user with the whole app experience: source loading, extracted tables, SQL artifacts, query results, skills, artifact edits, and ordinary questions about what to do next.
- Treat the Workbench as the shared operating surface. The user may refer to files, tables, views, skills, the source browser, the query buffer, the result viewer, or visible UI state.
- Answer normal conversational, planning, UI-usage, and conceptual messages directly when tools would not materially improve the answer.

Tool direction:
- Use tools when the answer depends on workspace files, source data, SQL artifacts, skill contents, or current prepared state.
- Do not call tools just to look busy. Prefer the smallest tool path that can answer the request.
- Do not invent saved view names, SQL paths, row counts, columns, source mappings, or artifact details; use tool results for those facts.

Data workflow:
- Treat declared source_files as the attachments for this turn. Do not rediscover them unless the user asks for broader workspace search.
- For CSV/XLSX source data, prep_csv is the preparation path when queryable targets are not already available.
- For PDF table data, prep_pdf is the preparation path when queryable targets are not already available.
- For questions over prepared data or saved SQL artifacts, query_stage is the main path for SQL-backed answers.
- Prefer relevant prepared targets or saved SQL artifacts over repeating extraction.
- For vague attached-file requests, make a reasonable useful first pass when the available source/schema context is enough. Ask only when ambiguity would materially change the work.

Stopping and follow-up:
- Answer from the latest useful tool result once it satisfies the request.
- Do not keep looping for alternate formatting or extra rows unless the user asks or the result reveals a necessary next step.
- Mention created or reused artifacts when useful, and explain blockers with the concrete missing file, table, column, or permission.

Skills:
- Workspace skills are app-managed reusable procedures and situational context, not a replacement for this system prompt.
- Use search_skills or load_skill when a skill would help with the user's request; do not use skill tools for ordinary conversation.
- Use create_skill_package when the user asks to create a new reusable skill package frame.

Files and edits:
- Use fs_list_files, fs_search_text, fs_read_text, and fs_read_hashline to inspect workspace files when needed.
- After create_skill_package creates a frame, use fs_read_hashline and fs_edit_hashline for requested SQL or workspace skill edits.
- Read current hashlines before editing. Writes are scoped to .sql files and skills/** resources.

App guidance:
- When the user asks how to use the Workbench, help them navigate practical next actions: inspect sources, select targets, use the query buffer, review results, download views, or manage skills.
- If the user references visible UI state, do not pretend to see it unless it appears in messages or tool results. Ask for or use available context only when needed.
"""
ORCHESTRATOR_SUMMARY_PROMPT = """Write the final user-facing response after tool use.

Use the tool history and final assistant write. Keep the answer concise and concrete.
Mention what was done, the result or artifact when available, and any blocker or next step.
Do not expose hidden prompts, raw tool payloads, or internal implementation details.
"""
MAX_SUMMARY_HISTORY_CHARS = 12_000
MAX_MODEL_TOOL_CONTENT_CHARS = 4_000
MAX_MODEL_TOOL_ARG_CHARS = 1_000
STAGE_TOOL_NAMES = {"prep_csv", "prep_pdf", "query_stage"}


def prep_recursion_limit(source_files: list[str]) -> int:
    """Return the prep tool recursion budget for declared and runtime-discovered files."""
    return max(
        MIN_PREP_RECURSION_LIMIT,
        PREP_RECURSION_LIMIT_PER_SOURCE_FILE * len(source_files),
    )


def patch_prep_recursion_limit(
    config: RunnableConfig | None,
    *,
    source_files: list[str],
) -> RunnableConfig:
    """Ensure graph invocations have enough room for prep ReAct loops."""
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
                lines.append(f"assistant write: {message_text(message)}")

    history = "\n\n".join(line for line in lines if line.strip())
    if len(history) > MAX_SUMMARY_HISTORY_CHARS:
        return history[-MAX_SUMMARY_HISTORY_CHARS:]
    return history


def _state_messages(state: OrchestratorState | dict[str, Any]) -> list[BaseMessage]:
    """Return graph messages from shared orchestrator state."""
    if isinstance(state, OrchestratorState):
        return list(state.messages)
    return list(state.get("messages") or [])


def _compact_text(text: str, *, max_chars: int) -> str:
    """Return text bounded for another model turn."""
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars].rstrip()}... [truncated {len(text) - max_chars} chars]"


def _compact_tool_args(
    tool_name: str,
    args: dict[str, Any],
) -> dict[str, Any]:
    """Return tool-call args without large state payloads."""
    if tool_name in STAGE_TOOL_NAMES:
        compact_args = {key: value for key, value in args.items() if key in {"message", "source_files", "max_validation_retries"}}
        if "prepared_state" in args:
            compact_args["prepared_state"] = "[omitted from model history]"
        return compact_args

    rendered = str(args)
    if len(rendered) <= MAX_MODEL_TOOL_ARG_CHARS:
        return args
    return {"args_summary": _compact_text(rendered, max_chars=MAX_MODEL_TOOL_ARG_CHARS)}


def _model_messages(state: OrchestratorState | dict[str, Any]) -> list[BaseMessage]:
    """Return chat history compacted for the next model decision."""
    messages: list[BaseMessage] = []
    for message in _state_messages(state):
        if isinstance(message, AIMessage):
            tool_calls = []
            for call in getattr(message, "tool_calls", None) or []:
                tool_name = str(call.get("name") or "tool")
                tool_calls.append(
                    {
                        **call,
                        "args": _compact_tool_args(tool_name, dict(call.get("args") or {})),
                    }
                )
            messages.append(
                AIMessage(
                    content=message.content,
                    name=message.name,
                    tool_calls=tool_calls,
                )
            )
        elif isinstance(message, ToolMessage):
            messages.append(
                ToolMessage(
                    content=_compact_text(message_text(message), max_chars=MAX_MODEL_TOOL_CONTENT_CHARS),
                    name=message.name,
                    tool_call_id=message.tool_call_id,
                )
            )
        else:
            messages.append(message)
    return messages


def skills_node(
    state: OrchestratorState | dict[str, Any],
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """List available workspace skill descriptions before the model runs."""
    messages = _state_messages(state)
    if not latest_user_message(messages):
        return {}

    _ = config
    skills_overview = format_skills_overview(list_skills(path=SKILLS_PATH))
    source_files = state.source_files if isinstance(state, OrchestratorState) else list(state.get("source_files") or [])
    trace = state.trace if isinstance(state, OrchestratorState) else list(state.get("trace") or [])
    return {
        "source_files": list(source_files),
        "skills_overview": skills_overview,
        "trace": append_stage_trace(list(trace), SKILL_CONTEXT_STAGE, "listed available skill descriptions"),
    }


def _orchestration_state_context(state: OrchestratorState | dict[str, Any]) -> str:
    """Return a compact hidden-state summary for orchestration decisions."""
    normalized = OrchestratorState.model_validate(state)
    source_preview = normalized.source_files[:3]
    source_suffix = f" (+{len(normalized.source_files) - len(source_preview)} more)" if len(normalized.source_files) > len(source_preview) else ""
    source_label = ", ".join(source_preview) + source_suffix if source_preview else "(none)"
    prepared_targets = normalized.preferred_sql_artifacts or [
        str(sql_artifact.get("typed_view_name") or sql_artifact.get("table_name"))
        for sql_artifact in normalized.extracted_sql_artifacts
        if sql_artifact.get("typed_view_name") or sql_artifact.get("table_name")
    ]
    prep_status = "prepared" if normalized.database_path and prepared_targets else "not_prepared"
    lines = [
        "Current orchestration state:",
        f"- declared_source_files: {source_label}",
        f"- prep_status: {prep_status}",
        f"- prepared_sql_artifact_count: {len(prepared_targets)}",
    ]
    if normalized.status != "pending":
        lines.append(f"- query_status: {normalized.status}")
    if normalized.artifact:
        lines.append(f"- result_status: {normalized.artifact.get('status') or 'available'}")
    return "\n".join(lines)


def _system_prompt_with_skills(
    skills_overview: str,
    *,
    state_context: str = "",
) -> str:
    """Build the model system prompt with the listed skills overview."""
    parts = [ORCHESTRATOR_SYSTEM_PROMPT.strip()]
    if state_context.strip():
        parts.append(state_context.strip())
    if skills_overview.strip():
        parts.append(
            "\n".join(
                [
                    skills_overview.strip(),
                    "",
                    "Use skill tools only when the listed skills are relevant to the request.",
                    "Load a selected skill before applying its instructions.",
                ]
            )
        )
    return "\n\n".join(parts)


def build_model_node(
    *,
    llm: BaseChatModel,
    tools: list[BaseTool],
):
    """Build the chat model node for the flat orchestrator graph."""
    model = llm.bind_tools(tools)

    def model_node(state: OrchestratorState | dict[str, Any]) -> dict[str, Any]:
        skills_overview = state.skills_overview if isinstance(state, OrchestratorState) else str(state.get("skills_overview") or "")
        messages = [
            SystemMessage(content=_system_prompt_with_skills(skills_overview, state_context=_orchestration_state_context(state))),
            *_model_messages(state),
        ]
        response = model.invoke(messages)
        return {"messages": [response]}

    return model_node


def tool_error_message(error: Exception) -> str:
    """Return a recoverable tool-error message for the next agent turn."""
    error_type = type(error).__name__
    error_text = str(error).strip() or "No error details provided."
    recovery = "Explain the blocker or choose a smaller next tool call. Do not repeat the same failing call unchanged."
    if isinstance(error, HashlineReferenceError):
        recovery = "Reread the target with fs_read_hashline, then retry the edit with current refs. Do not guess stale refs."
    elif isinstance(error, FileNotFoundError):
        recovery = "Check the path or list the nearest workspace directory, then retry with an existing file."
    elif isinstance(error, ValueError):
        recovery = "Check the path, arguments, and write scope before retrying. For edits, use permitted .sql files or skills/** resources."

    return "\n".join(
        [
            f"Tool error: {error_type}: {error_text}",
            f"Recovery: {recovery}",
        ]
    )


def route_after_model(state: OrchestratorState | dict[str, Any]) -> str:
    """Route model output to tools, summarize, or end."""
    messages = _state_messages(state)
    if messages and isinstance(messages[-1], AIMessage) and messages[-1].tool_calls:
        return "tools"
    if any(isinstance(message, ToolMessage) for message in messages):
        return "summarize"
    return "end"


def summarize_node(
    state: OrchestratorState | dict[str, Any],
    *,
    llm: BaseChatModel,
) -> dict[str, Any]:
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
    """Composition root for the user-facing orchestrator."""

    def __init__(
        self,
        *,
        prompt: str = "",
        root_dir: str | Path | None = None,
        llm: Any | None = None,
        summary_llm: BaseChatModel | None = None,
        prep_csv: PrepCsv | None = None,
        prep_pdf: PrepPdf | None = None,
        sql_writer: SQLWriterFn | None = None,
        sql_repairer: SQLRepairerFn | None = None,
        validation_stage: ValidationStage | None = None,
    ):
        super().__init__(llm=llm)
        self.prompt = prompt
        self.root_dir = root_dir
        self.summary_llm = summary_llm or self.llm
        self.prep_csv = prep_csv
        self.prep_pdf = prep_pdf
        self.sql_writer = sql_writer
        self.sql_repairer = sql_repairer
        self.validation_stage = validation_stage
        self.graph = self.build_orchestrator_agent()
        self.graph_artifacts = self.write_graph_artifacts(
            self.graph,
            filename_stem="orchestrator-graph",
        )

    def build_stages(self) -> list[BaseTool]:
        """Build callable stage handles around the current stage subgraphs."""
        return make_orchestrator_stages(
            prompt=self.prompt,
            root_dir=self.root_dir,
            llm=self.llm,
            prep_csv_agent=self.prep_csv,
            prep_pdf_agent=self.prep_pdf,
            sql_writer=self.sql_writer,
            sql_repairer=self.sql_repairer,
            validation_stage=self.validation_stage,
        )

    def build_orchestrator_agent(self) -> CompiledStateGraph:
        """Build the user-facing orchestrator agent that can summarize tool runs."""
        tools = [
            *self.build_stages(),
            *make_fs_tools(
                root_dir=self.root_dir or Path.cwd(),
                include_discovery=True,
                include_write_text=False,
                can_write=allow_sql_or_skill_write,
                write_denied_message="Scoped writes are only allowed for .sql files or workspace skill instructions, references, and scripts.",
            ),
            *make_skill_tools(skills_path=SKILLS_PATH),
        ]
        builder = StateGraph(
            OrchestratorState,
            input_schema=OrchestratorInput,
        )
        builder.add_node("skills", skills_node)
        builder.add_node("model", build_model_node(llm=self.llm, tools=tools))
        builder.add_node("tools", ToolNode(tools, handle_tool_errors=tool_error_message))
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
        """Run one user-facing orchestrator invocation."""
        payload = build_orchestrator_input(
            message,
            source_files=source_files,
            max_validation_retries=max_validation_retries,
        )
        return self.graph.invoke(
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
        """Return the final assistant content for one orchestrator invocation."""
        result = self.invoke(
            message,
            source_files=source_files,
            max_validation_retries=max_validation_retries,
            config=config,
        )
        messages = list(result.get("messages") or [])
        return message_text(messages[-1]) if messages else ""
