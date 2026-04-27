"""Prompt helpers for the prep stage."""

from __future__ import annotations

import json
from typing import Any

PREP_STAGE_SYSTEM_PROMPT = """
You are the prep stage for local data analysis.

Your job is to use the available prep tools to prepare the supplied source files into a shared SQLite database that downstream SQL analysis can use.

Use the available prep tools to inspect, profile, and extract the supplied files in whatever order helps you understand them best. You can revisit inspect and profile whenever needed before or after extraction attempts.

Try to finish the extraction in the current prep stage run instead of saving extraction work for a later run.
Once `extract_tabular` succeeds with a usable shared SQLite result, stop using tools and return `status="prepared"`.
Do not continue inspecting after a successful extraction unless the extraction output is clearly unusable.

Do not invent source files, tables, sheets, or outputs. If the available tools cannot safely prepare the data, stop and explain why.

Your structured response status must be one of:
- prepared: the extraction result is ready for downstream SQL work now.
- blocked: prep cannot proceed safely with the available tools or message clarity.
- error: prep failed in a non-retryable way.

Always include a short `summary` field in your structured response, even when the status is `prepared`.
""".strip()


def format_source_file_list(source_files: list[str]) -> str:
    """Render the source file list once for prompts."""
    return "\n".join(f"- {source_file}" for source_file in source_files) or "- (none)"


def summarize_skill_refs(skill_refs: list[dict[str, Any]]) -> str:
    """Render a compact summary of worker-visible skill references."""
    if not skill_refs:
        return ""

    lines = ["Skill refs available to prep:"]
    for skill_ref in skill_refs[:8]:
        skill_name = str(skill_ref.get("name", "unknown"))
        instructions = skill_ref.get("instructions") or {}
        instruction_path = str(instructions.get("relative_path") or skill_ref.get("path") or "")
        reference_count = len(skill_ref.get("references", [])) if isinstance(skill_ref.get("references"), list) else 0
        script_count = len(skill_ref.get("scripts", [])) if isinstance(skill_ref.get("scripts"), list) else 0
        summary_parts = [instruction_path] if instruction_path else []
        if reference_count:
            summary_parts.append(f"{reference_count} refs")
        if script_count:
            summary_parts.append(f"{script_count} scripts")
        lines.append(f"- {skill_name}: {', '.join(summary_parts) if summary_parts else 'loaded'}")
    if len(skill_refs) > 8:
        lines.append(f"- ... (+{len(skill_refs) - 8} more)")
    return "\n".join(lines)


def build_prep_request(
    prompt: str,
    message: str,
    source_files: list[str],
    *,
    prep_attempt: int,
    max_prep_trials: int,
    worker_instructions: str,
    skill_refs: list[dict[str, Any]],
    previous_attempts: list[str],
    retry_instructions: list[str],
) -> str:
    """Build the prep-stage user message."""
    parts = [prompt.strip()] if prompt.strip() else []
    if max_prep_trials == 1:
        parts.append("Prep stage run.")
    else:
        parts.append(f"Prep trial {prep_attempt} of {max_prep_trials}.")
    parts.append(f"Source files:\n{format_source_file_list(source_files)}\nMessage: {message}")
    if worker_instructions.strip():
        parts.append(worker_instructions.strip())
    skill_ref_summary = summarize_skill_refs(skill_refs)
    if skill_ref_summary:
        parts.append(skill_ref_summary)
    if previous_attempts:
        parts.append("Previous prep trials:\n" + "\n".join(f"- {attempt}" for attempt in previous_attempts))
    if retry_instructions:
        parts.append("Retry instructions for this prep trial:\n" + "\n".join(f"- {instruction}" for instruction in retry_instructions))
    parts.append(
        "Return the data in the final extraction shape best suited for the message. Use tool observations instead of guessing, and try to complete the extraction in this run."
    )
    return "\n\n".join(parts)


def parse_tool_content(content: str | list[dict[str, Any]]) -> dict[str, Any] | None:
    """Parse JSON-serialized tool content back into a dict when possible."""
    if not isinstance(content, str):
        return None
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
