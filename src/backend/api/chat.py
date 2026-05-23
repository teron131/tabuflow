"""Chat response helpers for the workbench API."""

from collections.abc import Iterator
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from ..agents.orchestrator.orchestrator import Orchestrator
from ..agents.orchestrator.state import message_text
from ..clients.multimodal import MediaMessage
from ..clients.openai import ChatOpenAI
from ..config import (
    DEFAULT_REASONING_EFFORT,
    MISSING_LLM_CONFIG_MESSAGE,
    REPO_ROOT,
    UPLOADS_DIR,
    has_llm_environment,
    resolve_agent_model,
)
from .constants import IMAGE_UPLOAD_EXTENSIONS
from .schemas import ChatHistoryMessage, ChatRequest

MAX_CHAT_HISTORY_MESSAGES = 16
MAX_TRACE_CONTENT_CHARS = 500
MAX_TOOL_TRACE_SUMMARY_CHARS = 220
MAX_CHAT_IMAGE_ATTACHMENTS = 4
TRACE_TOOL_LABEL_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
TRACE_PREFIX_RE = re.compile(r"^(?P<name>[a-z][a-z0-9]*(?:_[a-z0-9]+)*)\s*:\s*(?P<summary>.*)$")
MAX_CHAT_IMAGE_BYTES = 8 * 1024 * 1024
BACKEND_CHAT_TOOL_NAME = "backendChat"


class ChatConfigurationError(RuntimeError):
    """Raised when model-backed chat cannot run with the current environment."""


class ChatRuntimeError(RuntimeError):
    """Raised when model-backed chat fails during execution."""


@dataclass(frozen=True)
class ChatTurnContext:
    """Resolved runtime inputs shared by sync and streaming chat paths."""

    model_name: str
    image_paths: list[Path]
    history: list[BaseMessage]
    graph_input: dict[str, Any]


def _uploaded_source_path(source_file: str) -> Path | None:
    """Resolve a chat source file only when it points inside the uploads area."""
    source_path = Path(source_file).expanduser()
    if not source_path.is_absolute():
        source_path = REPO_ROOT / source_path
    try:
        resolved_path = source_path.resolve()
        resolved_path.relative_to(UPLOADS_DIR.resolve())
    except (OSError, ValueError):
        return None
    return resolved_path if resolved_path.is_file() else None


def _image_source_paths(source_files: list[str]) -> list[Path]:
    """Return valid uploaded image paths for one chat turn."""
    image_paths: list[Path] = []
    for source_file in source_files:
        source_path = _uploaded_source_path(source_file)
        if source_path is None or source_path.suffix.lower() not in IMAGE_UPLOAD_EXTENSIONS:
            continue
        try:
            if source_path.stat().st_size > MAX_CHAT_IMAGE_BYTES:
                continue
        except OSError:
            continue
        image_paths.append(source_path)
        if len(image_paths) >= MAX_CHAT_IMAGE_ATTACHMENTS:
            break
    return image_paths


def _human_message(
    latest_message: str,
    image_paths: list[Path],
) -> HumanMessage:
    """Create the latest user message, using MediaMessage when images are attached."""
    if image_paths:
        return MediaMessage(
            paths=image_paths,
            description=latest_message,
        )
    return HumanMessage(content=latest_message)


def assistant_content(message: BaseMessage | dict[str, Any]) -> str:
    """Return readable assistant text from string or content-block messages."""
    content = message.content if isinstance(message, BaseMessage) else message.get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                item_text = item.get("text")
                if item_text:
                    parts.append(str(item_text))
            elif isinstance(item, str):
                parts.append(item)
        text = "\n".join(parts)
    else:
        text = message_text(message)
    return text.strip()


def _chat_history(
    messages: list[ChatHistoryMessage],
    latest_message: str,
    image_paths: list[Path],
) -> list[BaseMessage]:
    """Convert browser-visible history into LangChain chat messages."""
    history: list[BaseMessage] = []
    for message in messages[-MAX_CHAT_HISTORY_MESSAGES:]:
        content = message.content.strip()
        if not content:
            continue
        if message.role == "user":
            history.append(HumanMessage(content=content))
        elif message.role == "assistant":
            history.append(AIMessage(content=content))

    if history and isinstance(history[-1], HumanMessage) and message_text(history[-1]) == latest_message:
        history[-1] = _human_message(latest_message, image_paths)
    else:
        history.append(_human_message(latest_message, image_paths))
    return history


def _message_trace(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """Return a compact visible trace of the chat spine sent to the model."""
    trace: list[dict[str, str]] = []
    for message_idx, message in enumerate(messages, start=1):
        role = "assistant" if isinstance(message, AIMessage) else "user"
        trace.append(
            {
                "id": f"{message_idx}:{role}",
                "role": role,
                "content": message_text(message)[:MAX_TRACE_CONTENT_CHARS],
            }
        )
    return trace


def _truncated_summary(message: BaseMessage) -> str:
    """Return a short one-line summary for a tool result."""
    text = message_text(message).replace("\n", " ").strip()
    if len(text) <= MAX_TOOL_TRACE_SUMMARY_CHARS:
        return text
    return f"{text[:MAX_TOOL_TRACE_SUMMARY_CHARS].rstrip()}..."


def _tool_trace(messages: list[BaseMessage]) -> list[dict[str, str]]:
    """Return the ordered backend tools used during one chat turn."""
    trace: list[dict[str, str]] = []
    pending_by_id: dict[str, dict[str, str]] = {}

    for message in messages:
        if isinstance(message, AIMessage):
            for tool_call in getattr(message, "tool_calls", None) or []:
                name = str(tool_call.get("name") or "tool")
                tool_call_id = str(tool_call.get("id") or "")
                item = {
                    "id": tool_call_id or f"{len(trace) + 1}:{name}",
                    "name": name,
                    "status": "called",
                    "summary": "",
                }
                trace.append(item)
                if tool_call_id:
                    pending_by_id[tool_call_id] = item
        elif isinstance(message, ToolMessage):
            tool_call_id = str(getattr(message, "tool_call_id", "") or "")
            item = pending_by_id.get(tool_call_id)
            if item is None:
                item = {
                    "id": tool_call_id or f"{len(trace) + 1}:{message.name or 'tool'}",
                    "name": message.name or "tool",
                    "status": "completed",
                    "summary": "",
                }
                trace.append(item)
            item["status"] = "completed"
            item["summary"] = _truncated_summary(message)

    return trace


def _stage_trace(result: dict[str, Any]) -> list[dict[str, str]]:
    """Return compact workflow stage trace entries, when the workflow produced them."""
    entries: list[dict[str, str]] = []
    for item in result.get("trace") or []:
        text = str(item).strip()
        if not text:
            continue
        stage, _, summary = text.partition(":")
        stage_name = stage.strip() or "stage"
        summary = summary.strip() or text
        trace_name, trace_summary = _stage_trace_fields(stage_name, summary)
        entries.append(
            {
                "id": f"{len(entries) + 1}:{stage_name}",
                "name": trace_name,
                "status": "completed",
                "summary": trace_summary,
            }
        )
    return entries


def _stage_trace_fields(stage: str, summary: str) -> tuple[str, str]:
    """Return the display name and summary for one workflow trace entry."""
    if match := TRACE_PREFIX_RE.match(summary):
        return match.group("name"), match.group("summary").strip() or summary
    if match := TRACE_TOOL_LABEL_RE.search(summary):
        return match.group(0), summary
    return stage, summary


def _tool_stage_trace(message: ToolMessage) -> list[dict[str, str]]:
    """Return nested stage trace rows from a JSON tool output."""
    try:
        payload = json.loads(message_text(message))
    except json.JSONDecodeError:
        return []
    return _stage_trace(payload) if isinstance(payload, dict) else []


def _dedupe_trace_items(
    trace_items: list[dict[str, str]],
    seen: set[str],
) -> list[dict[str, str]]:
    """Return trace rows that have not already been streamed this turn."""
    deduped_items: list[dict[str, str]] = []
    for item in trace_items:
        key = f"{item.get('name', '')}\n{item.get('summary', '')}"
        if key in seen:
            continue
        seen.add(key)
        deduped_items.append(item)
    return deduped_items


def _chunk_id() -> str:
    """Return a compact random ID for UI-message stream chunks."""
    return uuid4().hex


def _tool_output_payload(
    tool_name: str,
    tool_call_id: str,
    message: ToolMessage,
    *,
    stage_trace: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build one streamed tool result payload using the existing backend trace shape."""
    summary = _truncated_summary(message)
    artifact: dict[str, Any] = {
        "tool_trace": [
            {
                "id": tool_call_id,
                "name": tool_name,
                "status": "completed",
                "summary": summary,
            }
        ]
    }
    if stage_trace:
        artifact["stage_trace"] = stage_trace
    return {
        "status": "ok",
        "mode": "model_stream",
        "content": summary,
        "artifact": artifact,
    }


def _stream_tool_input(tool_call: dict[str, Any]) -> dict[str, Any]:
    """Return one AI SDK tool-input chunk for a backend tool call."""
    tool_name = str(tool_call.get("name") or "tool")
    return {
        "type": "tool-input-available",
        "toolCallId": str(tool_call.get("id") or _chunk_id()),
        "toolName": BACKEND_CHAT_TOOL_NAME,
        "input": {
            "tool": tool_name,
            "args": tool_call.get("args") or {},
        },
    }


def _stream_tool_output(
    message: ToolMessage,
    *,
    emitted_stage_traces: set[str],
) -> dict[str, Any]:
    """Return one AI SDK tool-output chunk for a backend tool result."""
    tool_call_id = str(getattr(message, "tool_call_id", "") or _chunk_id())
    tool_name = str(message.name or "tool")
    stage_trace = _dedupe_trace_items(_tool_stage_trace(message), emitted_stage_traces)
    return {
        "type": "tool-output-available",
        "toolCallId": tool_call_id,
        "output": _tool_output_payload(
            tool_name,
            tool_call_id,
            message,
            stage_trace=stage_trace,
        ),
    }


def _stream_error_output(error_text: str, tool_call_id: str | None = None) -> dict[str, Any]:
    """Return one AI SDK tool-output-error chunk for a backend chat failure."""
    return {
        "type": "tool-output-error",
        "toolCallId": tool_call_id or _chunk_id(),
        "errorText": error_text,
    }


def _stream_text_chunks(content: str, text_id: str) -> list[dict[str, Any]]:
    """Return AI SDK text chunks for a non-empty assistant message."""
    if not content:
        return []
    return [
        {"type": "text-start", "id": text_id},
        {"type": "text-delta", "id": text_id, "delta": content},
        {"type": "text-end", "id": text_id},
    ]


def _chat_turn_context(request: ChatRequest) -> ChatTurnContext:
    """Resolve request data that both chat execution modes need."""
    if not has_llm_environment():
        raise ChatConfigurationError(MISSING_LLM_CONFIG_MESSAGE)

    latest_message = request.message.strip()
    image_paths = _image_source_paths(request.source_files)
    history = _chat_history(
        request.messages,
        latest_message,
        image_paths,
    )
    return ChatTurnContext(
        model_name=resolve_agent_model(request.model),
        image_paths=image_paths,
        history=history,
        graph_input={
            "messages": history,
            "source_files": request.source_files,
        },
    )


def _chat_graph(model_name: str):
    """Build the orchestrator graph for one resolved model name."""
    llm = ChatOpenAI(model=model_name, temperature=0, reasoning_effort=DEFAULT_REASONING_EFFORT)
    return Orchestrator(
        llm=llm,
        root_dir=REPO_ROOT,
    ).build_orchestrator_agent()


def _messages_from_update(update: dict[str, Any]) -> Iterator[BaseMessage]:
    """Yield LangChain messages from one LangGraph update payload."""
    for node_update in update.values():
        if not isinstance(node_update, dict):
            continue
        for message in node_update.get("messages") or []:
            if isinstance(message, BaseMessage):
                yield message


def _tool_call_id(tool_call: dict[str, Any]) -> str:
    """Return the stable tool-call ID when LangChain supplied one."""
    return str(tool_call.get("id") or "")


def stream_chat_chunks(request: ChatRequest) -> Iterator[dict[str, Any]]:
    """Run one chat request and yield AI SDK chunks as graph updates arrive."""
    context = _chat_turn_context(request)
    graph = _chat_graph(context.model_name)

    yield {"type": "start", "messageId": _chunk_id()}
    text_id = _chunk_id()
    final_content = ""
    emitted_tool_inputs: set[str] = set()
    emitted_tool_outputs: set[str] = set()
    emitted_stage_traces: set[str] = set()

    try:
        for update in graph.stream(context.graph_input, stream_mode="updates"):
            for message in _messages_from_update(update):
                if isinstance(message, AIMessage):
                    tool_calls = getattr(message, "tool_calls", None) or []
                    for tool_call in tool_calls:
                        tool_call_id = _tool_call_id(tool_call)
                        if tool_call_id in emitted_tool_inputs:
                            continue
                        yield _stream_tool_input(tool_call)
                        if tool_call_id:
                            emitted_tool_inputs.add(tool_call_id)
                    content = assistant_content(message)
                    if content and not tool_calls:
                        final_content = content
                elif isinstance(message, ToolMessage):
                    tool_call_id = str(getattr(message, "tool_call_id", "") or "")
                    if tool_call_id and tool_call_id in emitted_tool_outputs:
                        continue
                    yield _stream_tool_output(
                        message,
                        emitted_stage_traces=emitted_stage_traces,
                    )
                    if tool_call_id:
                        emitted_tool_outputs.add(tool_call_id)
    except Exception as exc:
        yield _stream_error_output(f"Model-backed chat failed: {exc}")
        yield {"type": "finish", "finishReason": "error"}
        return

    yield from _stream_text_chunks(final_content, text_id)
    yield {"type": "finish", "finishReason": "stop"}


def run_chat(request: ChatRequest) -> dict[str, Any]:
    """Run one chat request through the orchestrator."""
    context = _chat_turn_context(request)
    graph = _chat_graph(context.model_name)

    try:
        result = graph.invoke(context.graph_input)
    except Exception as exc:
        raise ChatRuntimeError(f"Model-backed chat failed: {exc}") from exc

    messages = list(result.get("messages") or [])
    content = assistant_content(messages[-1]) if messages else ""
    if not content:
        raise ChatRuntimeError("Model-backed chat returned an empty response.")
    return {
        "status": "ok",
        "mode": "model",
        "content": content,
        "artifact": {
            "model": context.model_name,
            "tool_trace": _tool_trace(messages),
            "stage_trace": _stage_trace(result),
            "conversation_trace": _message_trace(context.history),
            "message_count": len(messages),
            "input_message_count": len(context.history),
            "source_file_count": len(request.source_files),
            "image_source_count": len(context.image_paths),
        },
    }
