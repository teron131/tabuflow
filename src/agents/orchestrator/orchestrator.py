"""Public entrypoints for the top-level orchestrator graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langchain.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.graph.state import CompiledStateGraph

from ..base import ApplicationAgent
from .middleware import SkillsContextMiddleware
from .prompts import build_system_prompt
from .state import OrchestratorState
from .tools import make_orchestrator_tools


class Orchestrator(ApplicationAgent):
    """Top-level chatbot orchestrator built as a LangGraph agent runtime."""

    default_model_order = ("main_llm", "fast_llm", "quality_llm")

    def __init__(
        self,
        *,
        prompt: str = "",
        root_dir: str | Path | None = None,
    ):
        super().__init__()
        self.prompt = prompt
        self.root_dir = root_dir
        self.graph = self.build_graph()
        self.graph_artifacts = self.write_graph_artifacts(
            self.graph,
            filename_stem="orchestrator-graph",
        )

    def build_graph(self) -> CompiledStateGraph:
        """Build the compiled orchestrator graph."""
        tools = make_orchestrator_tools(
            prompt=self.prompt,
            root_dir=self.root_dir,
            llm=self.llm,
        )
        return create_agent(
            model=self.llm,
            tools=tools,
            system_prompt=build_system_prompt(self.prompt),
            middleware=[SkillsContextMiddleware()],
            state_schema=OrchestratorState,
            name="orchestrator",
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
        response_messages = reversed(result["messages"])
        response_message = next(
            (item for item in response_messages if isinstance(item, AIMessage) and not getattr(item, "tool_calls", None)),
            None,
        )
        if response_message is None:
            raise RuntimeError("Orchestrator completed without a final AI response.")
        return str(response_message.text).strip()
