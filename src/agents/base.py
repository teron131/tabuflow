"""Shared base helpers for application-owned agents."""

from __future__ import annotations

from typing import Any, TypeVar

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel

from ..clients.openai import ChatOpenAI
from ..utils import write_langgraph_artifacts
from .config import DEFAULT_REASONING_EFFORT, resolve_agent_model

StructuredResponse = TypeVar("StructuredResponse", bound=BaseModel)


class ApplicationAgent:
    """Base class for agents that share model resolution and graph artifacts."""

    def __init__(
        self,
        *,
        llm: BaseChatModel | None = None,
        model: str | None = None,
        temperature: float = 0,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ):
        self.llm = llm or self.build_llm(
            model=model,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )

    def resolve_model(self, model: str | None = None) -> str:
        """Resolve the configured model name for this agent."""
        return resolve_agent_model(model)

    def build_llm(
        self,
        *,
        model: str | None = None,
        temperature: float = 0,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ) -> BaseChatModel:
        """Build the OpenAI-compatible chat model for this agent."""
        return ChatOpenAI(
            model=self.resolve_model(model),
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )

    def write_graph_artifacts(
        self,
        graph: CompiledStateGraph,
        *,
        filename_stem: str,
    ) -> dict[str, str | None]:
        """Persist graph visualization artifacts when rendering is available."""
        return write_langgraph_artifacts(graph, filename_stem=filename_stem)

    def build_structured_agent(
        self,
        schema: type[StructuredResponse],
        *,
        system_prompt: str,
        name: str,
    ) -> CompiledStateGraph:
        """Build a schema-returning agent with LangChain's tool strategy."""
        return create_agent(
            model=self.llm,
            tools=[],
            system_prompt=system_prompt,
            response_format=ToolStrategy(schema),
            name=name,
        )

    @staticmethod
    def get_structured_response(
        result: dict[str, Any],
        schema: type[StructuredResponse],
        *,
        agent_name: str,
    ) -> StructuredResponse:
        """Extract and validate the Pydantic structured response from an agent run."""
        structured_response = result.get("structured_response")
        if structured_response is None:
            raise RuntimeError(f"{agent_name} completed without a structured response.")
        return schema.model_validate(structured_response)
