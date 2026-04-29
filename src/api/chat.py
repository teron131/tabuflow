"""Chat response helpers for the workbench API."""

import os
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage

from ..agents.orchestrator.orchestrator import Orchestrator
from ..agents.orchestrator.state import message_text
from .schemas import ChatRequest

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
    return text.encode("ascii", errors="ignore").decode().strip()


def run_chat(request: ChatRequest) -> dict[str, Any]:
    """Run one chat request through the orchestrator."""
    if not has_llm_environment():
        raise ChatConfigurationError(MISSING_LLM_CONFIG_MESSAGE)

    try:
        result = (
            Orchestrator()
            .build_orchestrator_agent()
            .invoke(
                {
                    "messages": [HumanMessage(content=request.message)],
                    "source_files": [],
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
            "message_count": len(messages),
        },
    }
