"""LangChain tools exposed to the top-level orchestrator agent."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from langchain.messages import ToolMessage
from langchain.tools import BaseTool, tool
from langchain_core.runnables import RunnableConfig
from langchain_core.runnables.config import patch_config
from langchain_core.tools import InjectedToolArg, InjectedToolCallId
from langgraph.types import Command

from ...tools import load_skills
from ..prep_agent import PrepAgent
from ..sql_agent import SQLAgent
from ..validation_agent import ValidationAgent
from .workflow import SQL_AGENT_NAME, execute_workflow


def _summarize_sql_output(output: dict[str, Any]) -> str:
    """Render a concise summary of the SQL worker output."""
    status = str(output.get("status", "pending"))
    selected_targets = [str(item) for item in output.get("selected_targets", [])]
    result = output.get("result") or {}

    if status == "complete":
        lines = ["SQL workflow completed."]
        if selected_targets:
            lines.append(f"Targets: {', '.join(selected_targets[:4])}")
        row_count = result.get("row_count")
        if row_count is not None:
            lines.append(f"Rows: {row_count}")
        if summary := result.get("summary"):
            lines.append(f"Summary: {summary}")
        return "\n".join(lines)

    last_error = output.get("last_error")
    if last_error:
        return f"SQL workflow ended with status={status}: {last_error}"
    return f"SQL workflow ended with status={status}."


def _agent_command(
    *,
    tool_name: str,
    tool_call_id: str,
    content: str,
    latest_artifact: dict[str, Any],
    active_agent: str | None,
    workflow_artifact: dict[str, Any] | None = None,
    agent_artifacts: dict[str, dict[str, Any]] | None = None,
) -> Command:
    """Return one tool command that updates orchestrator state plus tool history."""
    update: dict[str, Any] = {
        "messages": [
            ToolMessage(
                content=content,
                tool_call_id=tool_call_id,
                name=tool_name,
            )
        ],
        "latest_artifact": latest_artifact,
        "active_agent": active_agent,
    }
    if workflow_artifact is not None:
        update["workflow_artifact"] = workflow_artifact
    if agent_artifacts is not None:
        update["agent_artifacts"] = agent_artifacts
    return Command(update=update)


def make_orchestrator_tools(
    *,
    prompt: str = "",
    root_dir: str | Path | None = None,
    llm: Any | None = None,
) -> list[BaseTool]:
    """Build the static tool set exposed to the top-level orchestrator."""
    prep_agent: PrepAgent | None = None
    sql_agent: SQLAgent | None = None
    validation_agent: ValidationAgent | None = None

    def get_prep_agent() -> PrepAgent:
        """Return one cached prep worker instance."""
        nonlocal prep_agent
        if prep_agent is None:
            prep_agent = PrepAgent(
                llm=llm,
                prompt=prompt,
                root_dir=root_dir,
            )
        return prep_agent

    def get_sql_agent() -> SQLAgent:
        """Return one cached SQL worker instance."""
        nonlocal sql_agent
        if sql_agent is None:
            sql_agent = SQLAgent(llm=llm) if llm is not None else SQLAgent()
        return sql_agent

    def get_validation_agent() -> ValidationAgent:
        """Return one cached validation worker instance."""
        nonlocal validation_agent
        if validation_agent is None:
            validation_agent = ValidationAgent(llm=llm) if llm is not None else ValidationAgent()
        return validation_agent

    @tool(parse_docstring=True)
    def run_sql_workflow(
        question: str,
        database_path: str,
        max_suggestions: int = 3,
        max_repairs: int = 2,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> Command:
        """Run the SQL agent workflow against an existing SQLite database.

        Args:
            question: Natural-language analysis question to answer with SQL.
            database_path: Absolute or relative SQLite database path.
            max_suggestions: Maximum number of candidate SQL targets to inspect.
            max_repairs: Maximum number of SQL repair attempts.
        """
        sql_agent = get_sql_agent()
        output = sql_agent.invoke(
            question,
            database_path=database_path,
            max_suggestions=max_suggestions,
            max_repairs=max_repairs,
            config=patch_config(config, run_name="sql_agent"),
        )
        artifact = output.model_dump(mode="json")
        return _agent_command(
            tool_name="run_sql_workflow",
            tool_call_id=tool_call_id,
            content=_summarize_sql_output(artifact),
            latest_artifact=artifact,
            active_agent=SQL_AGENT_NAME,
            agent_artifacts={SQL_AGENT_NAME: artifact},
        )

    @tool(parse_docstring=True)
    def run_workflow(
        task: str,
        source_files: list[str],
        max_prep_trials: int = 2,
        max_validation_retries: int = 2,
        tool_call_id: Annotated[str, InjectedToolCallId] = "",
        config: Annotated[RunnableConfig, InjectedToolArg] = None,
    ) -> Command:
        """Run the workflow on local files and return its final result.

        Args:
            task: Natural-language task to execute against the supplied files.
            source_files: One or more local spreadsheet or table-like file paths.
            max_prep_trials: Maximum number of prep-agent retries before stopping.
            max_validation_retries: Maximum number of validator-requested SQL retries.
        """
        workflow_result = execute_workflow(
            task=task,
            source_files=source_files,
            max_prep_trials=max_prep_trials,
            max_validation_retries=max_validation_retries,
            prompt=prompt,
            root_dir=root_dir,
            llm=llm,
            prep_agent=get_prep_agent(),
            sql_agent=get_sql_agent(),
            validation_agent=get_validation_agent(),
            config=config,
        )
        return _agent_command(
            tool_name="run_workflow",
            tool_call_id=tool_call_id,
            content=workflow_result.content,
            latest_artifact=workflow_result.artifact,
            active_agent=workflow_result.active_agent,
            workflow_artifact=workflow_result.artifact,
            agent_artifacts=workflow_result.agent_artifacts,
        )

    return [
        load_skills,
        run_sql_workflow,
        run_workflow,
    ]
