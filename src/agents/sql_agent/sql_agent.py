"""Minimal LangGraph orchestration for SQL question answering."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ..base import ApplicationAgent
from ..config import DEFAULT_REASONING_EFFORT
from .nodes import (
    clarify_node,
    execute_node,
    inspect_node,
    make_plan_node,
    repair_node,
    route_after_execute,
    route_after_inspect,
    route_after_plan,
    route_after_suggest,
    suggest_node,
)
from .payloads import build_planner_messages
from .prompts import SQL_PLANNER_SYSTEM_PROMPT
from .state import (
    PlannerFn,
    SQLAgentInput,
    SQLAgentOutput,
    SQLAgentState,
    SQLPlan,
)


class SQLAgent(ApplicationAgent):
    """Minimal LangGraph SQL agent that orchestrates the standalone SQL tools."""

    default_model_order = ("fast_llm", "main_llm", "quality_llm")

    def __init__(
        self,
        planner: PlannerFn | None = None,
        *,
        llm: BaseChatModel | None = None,
        model: str | None = None,
        temperature: float = 0,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
    ):
        """Initialize the SQL agent with the available tool set."""
        super().__init__(
            llm=llm,
            model=model,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
        )
        self.planner = planner or self.build_planner()
        self.graph = self.build_graph()
        self.graph_artifacts = self.write_graph_artifacts(
            self.graph,
            filename_stem="sql-agent-graph",
        )

    def build_planner(self) -> PlannerFn:
        """Build the structured SQL planner used by this agent."""
        planner_agent = self.build_structured_agent(
            SQLPlan,
            system_prompt=SQL_PLANNER_SYSTEM_PROMPT,
            name="sql_planner",
        )

        def planner(state: SQLAgentState) -> SQLPlan:
            """Plan the next SQL investigation step."""
            result = planner_agent.invoke({"messages": build_planner_messages(state)})
            return self.get_structured_response(
                result,
                SQLPlan,
                agent_name="sql_planner",
            )

        return planner

    def build_graph(self) -> CompiledStateGraph:
        """Build the compiled SQL workflow graph."""
        builder = StateGraph(
            SQLAgentState,
            input_schema=SQLAgentInput,
            output_schema=SQLAgentOutput,
        )
        builder.add_node("suggest", suggest_node)
        builder.add_node("clarify", clarify_node)
        builder.add_node("inspect", inspect_node)
        builder.add_node("plan", make_plan_node(self.planner))
        builder.add_node("execute", execute_node)
        builder.add_node("repair", repair_node)

        builder.add_edge(START, "suggest")
        builder.add_conditional_edges(
            "suggest",
            route_after_suggest,
            {
                "clarify": "clarify",
                "inspect": "inspect",
                END: END,
            },
        )
        builder.add_edge("clarify", END)
        builder.add_conditional_edges(
            "inspect",
            route_after_inspect,
            {
                "plan": "plan",
                END: END,
            },
        )
        builder.add_conditional_edges(
            "plan",
            route_after_plan,
            {
                "execute": "execute",
                END: END,
            },
        )
        builder.add_conditional_edges(
            "execute",
            route_after_execute,
            {
                "repair": "repair",
                END: END,
            },
        )
        builder.add_edge("repair", "plan")
        return builder.compile(name="sql_agent")

    def invoke(
        self,
        question: str,
        *,
        database_path: str | Path | None = None,
        preferred_targets: list[str] | None = None,
        source_files: list[str] | None = None,
        worker_context: str = "",
        skill_refs: list[dict[str, Any]] | None = None,
        validation_feedback: dict[str, Any] | None = None,
        max_suggestions: int = 3,
        max_repairs: int = 2,
        sample_rows: int = 3,
        text_value_hints: int = 3,
        config: RunnableConfig | None = None,
    ) -> SQLAgentOutput:
        """Run the SQL graph for one question."""
        result = self.graph.invoke(
            SQLAgentInput(
                question=question,
                database_path=(None if database_path is None else str(Path(database_path).expanduser().resolve())),
                preferred_targets=preferred_targets or [],
                source_files=source_files or [],
                worker_context=worker_context,
                skill_refs=skill_refs or [],
                validation_feedback=validation_feedback,
                max_suggestions=max_suggestions,
                max_repairs=max_repairs,
                sample_rows=sample_rows,
                text_value_hints=text_value_hints,
            ),
            config=config,
        )
        return SQLAgentOutput.model_validate(result)


def answer_sql_question(
    question: str,
    *,
    planner: PlannerFn | None = None,
    llm: BaseChatModel | None = None,
    model: str | None = None,
    database_path: str | Path | None = None,
    preferred_targets: list[str] | None = None,
    source_files: list[str] | None = None,
    worker_context: str = "",
    skill_refs: list[dict[str, Any]] | None = None,
    validation_feedback: dict[str, Any] | None = None,
    max_suggestions: int = 3,
    max_repairs: int = 2,
    sample_rows: int = 3,
    text_value_hints: int = 3,
    config: RunnableConfig | None = None,
) -> SQLAgentOutput:
    """Convenience wrapper for one-shot SQL agent execution."""
    agent = SQLAgent(
        planner=planner,
        llm=llm,
        model=model,
    )
    return agent.invoke(
        question,
        database_path=database_path,
        preferred_targets=preferred_targets,
        source_files=source_files,
        worker_context=worker_context,
        skill_refs=skill_refs,
        validation_feedback=validation_feedback,
        max_suggestions=max_suggestions,
        max_repairs=max_repairs,
        sample_rows=sample_rows,
        text_value_hints=text_value_hints,
        config=config,
    )
