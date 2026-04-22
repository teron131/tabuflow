"""Public entrypoints for the top-level orchestrator graph."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from langchain.messages import AIMessage, AnyMessage, HumanMessage

from ...clients.openai import ChatOpenAI
from ...utils import write_langgraph_artifacts
from .graph import create_orchestrator_graph

DEFAULT_MODEL_ENV = "MAIN_LLM"
FALLBACK_MODEL_ENV = "FAST_LLM"
DEFAULT_MODEL = "openai/gpt-5.4-nano"
DEFAULT_REASONING_EFFORT: Literal["minimal", "low", "medium", "high"] = "high"


def _resolve_model_name() -> str:
    """Resolve the orchestrator model from the environment."""
    return os.getenv(DEFAULT_MODEL_ENV) or os.getenv(FALLBACK_MODEL_ENV) or DEFAULT_MODEL


class Orchestrator:
    """Top-level chatbot orchestrator built as a LangGraph agent runtime."""

    def __init__(
        self,
        *,
        prompt: str = "",
        root_dir: str | Path | None = None,
    ):
        self.prompt = prompt
        self.root_dir = root_dir
        self.model = _resolve_model_name()
        self.llm = ChatOpenAI(
            model=self.model,
            temperature=0,
            reasoning_effort=DEFAULT_REASONING_EFFORT,
        )
        self.graph = self.build_graph()
        self.graph_artifacts = write_langgraph_artifacts(
            self.graph,
            filename_stem="orchestrator-graph",
        )

    def build_graph(self):
        """Build the compiled orchestrator graph."""
        return create_orchestrator_graph(
            llm=self.llm,
            prompt=self.prompt,
            root_dir=self.root_dir,
        )

    def invoke(self, message: str | list[AnyMessage | dict[str, Any]]) -> dict[str, Any]:
        """Run one orchestrator turn from a user message or message history."""
        if isinstance(message, str):
            messages: list[AnyMessage | dict[str, Any]] = [HumanMessage(content=message)]
        else:
            messages = message
        return self.graph.invoke({"messages": messages})

    def answer(self, message: str | list[AnyMessage | dict[str, Any]]) -> str:
        """Return the final assistant text for one orchestrator turn."""
        result = self.invoke(message)
        response_message = next(
            (item for item in reversed(result["messages"]) if isinstance(item, AIMessage) and not getattr(item, "tool_calls", None)),
            None,
        )
        if response_message is None:
            raise RuntimeError("Orchestrator completed without a final AI response.")
        return str(response_message.text).strip()
