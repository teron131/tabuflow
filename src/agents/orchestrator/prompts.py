"""Prompt and worker-context builders for orchestrator stages."""

from langchain_core.messages import HumanMessage

from ..prep_agent.prompts import build_prep_request
from .skill_context import format_skill_sql_references, summarize_skill_refs


def build_prep_stage_message(
    prompt: str,
    *,
    task: str,
    source_files: list[str],
    worker_instructions: str,
    skill_refs: list[dict],
) -> HumanMessage:
    """Build the first prep ReAct message for an orchestrator run."""
    return HumanMessage(
        content=build_prep_request(
            prompt,
            task,
            source_files,
            prep_attempt=1,
            max_prep_trials=1,
            worker_instructions=worker_instructions,
            skill_refs=skill_refs,
            previous_attempts=[],
            retry_instructions=[],
        )
    )


def build_sql_worker_context(
    prompt: str,
    *,
    worker_instructions: str,
    skill_refs: list[dict],
) -> str:
    """Build context that should inform SQL planning without redefining the task."""
    parts = [prompt.strip()] if prompt.strip() else []
    if worker_instructions.strip():
        parts.append(worker_instructions.strip())
    if skill_ref_summary := summarize_skill_refs(skill_refs):
        parts.append(skill_ref_summary)
    if sql_references := format_skill_sql_references(skill_refs):
        parts.append(sql_references)
    return "\n\n".join(parts)
