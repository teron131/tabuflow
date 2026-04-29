"""Chat response helpers for the workbench API."""

import os
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from ..agents.config import DEFAULT_REASONING_EFFORT, resolve_agent_model
from ..agents.orchestrator.orchestrator import Orchestrator
from ..agents.orchestrator.state import message_text
from ..clients.openai import ChatOpenAI
from .schemas import ChatHistoryMessage, ChatRequest

MAX_CHAT_HISTORY_MESSAGES = 16
MAX_TRACE_CONTENT_CHARS = 500
MAX_TOOL_TRACE_SUMMARY_CHARS = 220
REQUIRED_LLM_ENV_VARS = ("LLM_API_KEY", "LLM_BASE_URL")
MISSING_LLM_CONFIG_MESSAGE = "LLM_API_KEY and LLM_BASE_URL are required for chat."


def has_llm_environment() -> bool:
    """Return whether model-backed chat can be attempted."""
    return all(os.getenv(name) for name in REQUIRED_LLM_ENV_VARS)


class ChatConfigurationError(RuntimeError):
    """Raised when model-backed chat cannot run with the current environment."""


class ChatRuntimeError(RuntimeError):
    """Raised when model-backed chat fails during execution."""


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


def _chat_history(messages: list[ChatHistoryMessage], latest_message: str) -> list[BaseMessage]:
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

    if not history or not isinstance(history[-1], HumanMessage) or message_text(history[-1]) != latest_message:
        history.append(HumanMessage(content=latest_message))
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
        entries.append(
            {
                "id": f"{len(entries) + 1}:{stage.strip() or 'stage'}",
                "name": stage.strip() or "stage",
                "status": "completed",
                "summary": summary.strip() or text,
            }
        )
    return entries


def run_chat(request: ChatRequest) -> dict[str, Any]:
    """Run one chat request through the orchestrator."""
    if not has_llm_environment():
        raise ChatConfigurationError(MISSING_LLM_CONFIG_MESSAGE)

    latest_message = request.message.strip()
    history = _chat_history(request.messages, latest_message)
    model_name = resolve_agent_model(request.model)
    llm = ChatOpenAI(model=model_name, temperature=0, reasoning_effort=DEFAULT_REASONING_EFFORT)

    try:
        result = (
            Orchestrator(llm=llm)
            .build_orchestrator_agent()
            .invoke(
                {
                    "messages": history,
                    "source_files": request.source_files,
                }
            )
        )
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
            "model": model_name,
            "tool_trace": _tool_trace(messages),
            "stage_trace": _stage_trace(result),
            "conversation_trace": _message_trace(history),
            "message_count": len(messages),
            "input_message_count": len(history),
            "source_file_count": len(request.source_files),
        },
    }
