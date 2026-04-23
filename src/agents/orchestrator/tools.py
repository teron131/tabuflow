"""Tool wrappers exposed to the top-level orchestrator graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain.tools import BaseTool, tool

from ...tools import list_skills, load_skills, search_skills
from ..sql_agent import SQLAgent
from ..tabular_agent import TabularTaskAgent
from ..tabular_agent.payloads import build_result_message_content

SKILLS_PATH = "skills"


def list_skills_context(*, path: str = SKILLS_PATH) -> dict[str, Any]:
    """Return the raw workspace-skills listing payload."""
    return list_skills.invoke({"path": path})


def search_skills_context(
    query: str,
    *,
    path: str = SKILLS_PATH,
) -> dict[str, Any]:
    """Return the raw semantic workspace-skills search payload."""
    return search_skills.invoke(
        {
            "path": path,
            "query": query,
        }
    )


def format_skills_overview(result: dict[str, Any]) -> str:
    """Render a deterministic system-prompt section for available skills."""
    diagnostics = [str(item) for item in result.get("diagnostics", [])]
    if result.get("status") == "error":
        message = "; ".join(diagnostics) or "unknown error"
        return f"Workspace skills listing failed: {message}"

    skills = list(result.get("skills", []))
    lines = ["Workspace skills available under `skills`:"]
    if skills:
        for skill in skills:
            lines.append(f"- {skill.get('name', 'unknown')}: {str(skill.get('description', '')).strip()} ({skill.get('path', '')})")
    else:
        lines.append("- none found")

    if diagnostics:
        lines.append(f"Diagnostics: {'; '.join(diagnostics[:3])}")
    return "\n".join(lines)


def format_skill_matches(result: dict[str, Any]) -> str:
    """Render a deterministic user-turn section for relevant skills."""
    diagnostics = [str(item) for item in result.get("diagnostics", [])]
    if result.get("status") == "error":
        message = "; ".join(diagnostics) or "unknown error"
        return f"Workspace skills search failed: {message}"

    skills = list(result.get("skills", []))
    lines = []
    if skills:
        for skill in skills:
            score = skill.get("score")
            suffix = f", score={score}" if score is not None else ""
            lines.append(f"- {skill.get('name', 'unknown')}: {str(skill.get('description', '')).strip()} ({skill.get('path', '')}{suffix})")
    else:
        lines.append("- none above threshold")

    if diagnostics:
        lines.append(f"Diagnostics: {'; '.join(diagnostics[:3])}")
    return "\n".join(lines)


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


def _build_worker_skill_payload(
    task: str,
    *,
    path: str = SKILLS_PATH,
) -> dict[str, Any]:
    """Search and load matched skills into one worker-ready payload."""
    search_result = search_skills_context(task, path=path)
    matched_skills = list(search_result.get("skills", []))
    matched_skill_names: list[str] = []
    worker_sections: list[str] = []
    skill_refs: list[dict[str, Any]] = []
    diagnostics = [str(item) for item in search_result.get("diagnostics", [])]

    for skill in matched_skills:
        skill_name = str(skill.get("name", "")).strip()
        if not skill_name:
            continue
        matched_skill_names.append(skill_name)

        load_result = load_skills.invoke(
            {
                "path": path,
                "skills": skill_name,
            }
        )
        diagnostics.extend(str(item) for item in load_result.get("diagnostics", []))
        loaded_skills = list(load_result.get("skills", []))
        if not loaded_skills:
            continue
        loaded_skill = loaded_skills[0]

        description = str(loaded_skill.get("description", "")).strip()
        instructions_payload = loaded_skill.get("instructions") or {}
        instructions = str(instructions_payload.get("content", "")).strip()
        if instructions:
            worker_sections.append(f"Skill `{loaded_skill.get('name', 'unknown')}`: {description}".strip())
            worker_sections.append(instructions)
        skill_refs.append(loaded_skill)

    if diagnostics:
        worker_sections.append("Skill loading diagnostics:")
        worker_sections.extend(f"- {message}" for message in diagnostics)

    return {
        "matched_skill_names": matched_skill_names,
        "worker_instructions": "\n\n".join(section for section in worker_sections if section.strip()),
        "skill_refs": skill_refs,
    }


def make_orchestrator_tools(
    *,
    prompt: str = "",
    root_dir: str | Path | None = None,
    llm: Any | None = None,
) -> list[BaseTool]:
    """Build the static tool set exposed to the top-level orchestrator."""
    tabular_agent: TabularTaskAgent | None = None
    sql_agent: SQLAgent | None = None

    def get_tabular_agent() -> TabularTaskAgent:
        """Return one cached tabular worker instance."""
        nonlocal tabular_agent
        if tabular_agent is None:
            tabular_agent = TabularTaskAgent(
                prompt=prompt,
                root_dir=root_dir,
            )
        return tabular_agent

    def get_sql_agent() -> SQLAgent:
        """Return one cached SQL worker instance."""
        nonlocal sql_agent
        if sql_agent is None:
            sql_agent = SQLAgent(llm=llm) if llm is not None else SQLAgent()
        return sql_agent

    @tool(parse_docstring=True, response_format="content_and_artifact")
    def run_sql_workflow(
        question: str,
        database_path: str,
        max_suggestions: int = 3,
        max_repairs: int = 2,
    ) -> tuple[str, dict[str, Any]]:
        """Run the SQL worker workflow against an existing SQLite database.

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
        )
        artifact = output.model_dump(mode="json")
        return _summarize_sql_output(artifact), artifact

    @tool(parse_docstring=True, response_format="content_and_artifact")
    def run_tabular_workflow(
        task: str,
        source_files: list[str],
        max_prep_trials: int = 2,
        max_validation_retries: int = 2,
    ) -> tuple[str, dict[str, Any]]:
        """Run the tabular worker workflow on local files and return its final result.

        Args:
            task: Natural-language task to execute against the supplied files.
            source_files: One or more local spreadsheet or table-like file paths.
            max_prep_trials: Maximum number of prep-agent retries before stopping.
            max_validation_retries: Maximum number of validator-requested SQL retries.
        """
        tabular_agent = get_tabular_agent()
        skill_payload = _build_worker_skill_payload(task)
        output = tabular_agent.invoke(
            task,
            source_files=source_files,
            matched_skill_names=skill_payload["matched_skill_names"],
            worker_instructions=skill_payload["worker_instructions"],
            skill_refs=skill_payload["skill_refs"],
            run_id=uuid4().hex[:8],
            max_prep_trials=max_prep_trials,
            max_validation_retries=max_validation_retries,
        )
        artifact = output.result_artifact or output.model_dump(mode="json")
        return build_result_message_content(artifact), artifact

    return [
        load_skills,
        run_sql_workflow,
        run_tabular_workflow,
    ]
