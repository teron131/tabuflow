"""Prompt and worker-context builders for orchestrator stages."""

from langchain_core.messages import HumanMessage

from ..prep_stage.prompts import build_prep_request
from .skill_context import format_skill_references_for_sql, summarize_skill_refs


def build_user_request_message(
    *,
    message: str,
    source_files: list[str],
) -> HumanMessage:
    """Package the public chat entrypoint as the first user message."""
    source_list = "\n".join(f"- {source_file}" for source_file in source_files) or "- (none)"
    return HumanMessage(
        content=f"{message.strip() or '(empty message)'}\n\nDeclared source files:\n{source_list}",
        name="user",
    )


def build_prep_stage_message(
    prompt: str,
    *,
    message: str,
    source_files: list[str],
    worker_instructions: str,
    skill_refs: list[dict],
) -> HumanMessage:
    """Build the first prep ReAct message for an orchestrator run."""
    return HumanMessage(
        content=build_prep_request(
            prompt,
            message,
            source_files,
            prep_attempt=1,
            max_prep_trials=1,
            worker_instructions=worker_instructions,
            skill_refs=skill_refs,
            previous_attempts=[],
            retry_instructions=[],
        ),
        name="prep_stage",
    )


def build_sql_worker_context(
    prompt: str,
    *,
    worker_instructions: str,
    skill_refs: list[dict],
) -> str:
    """Build context that should inform SQL planning without redefining the message."""
    parts = [prompt.strip()] if prompt.strip() else []
    if worker_instructions.strip():
        parts.append(worker_instructions.strip())
    if skill_ref_summary := summarize_skill_refs(skill_refs):
        parts.append(skill_ref_summary)
    if skill_references := format_skill_references_for_sql(skill_refs):
        parts.append(skill_references)
    return "\n\n".join(parts)
