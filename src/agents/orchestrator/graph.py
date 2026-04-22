"""Graph builder for the top-level orchestrator agent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain.agents import create_agent
from langgraph.graph.state import CompiledStateGraph

from .middleware import SkillsContextMiddleware
from .prompts import build_system_prompt
from .state import OrchestratorState
from .tools import make_orchestrator_tools


def create_orchestrator_graph(
    *,
    llm: Any,
    prompt: str = "",
    root_dir: str | Path | None = None,
) -> CompiledStateGraph:
    """Create the top-level messages-first orchestrator graph."""
    tools = make_orchestrator_tools(
        prompt=prompt,
        root_dir=root_dir,
        llm=llm,
    )
    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=build_system_prompt(prompt),
        middleware=[SkillsContextMiddleware()],
        state_schema=OrchestratorState,
        name="orchestrator",
    )
