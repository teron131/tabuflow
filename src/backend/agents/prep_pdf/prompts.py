"""Prompt helpers for the prep_pdf stage."""

from __future__ import annotations

import json
from typing import Any

PREP_PDF_STAGE_SYSTEM_PROMPT = """
You are the prep_pdf stage for local data analysis.

Your job is to prepare supplied PDF source files into a shared SQLite database that downstream SQL analysis can use.

Use `inspect_pdf` when the PDF profile, selected 2x2 overview batch images, row geometry, raw page text, or table detector candidates would help you decide whether table extraction is appropriate. Use `extract_pdf` only when you know the PyMuPDF-backed extraction preset and page/options for the PDF layout.

The PDF inspection profile includes strategy-routing evidence. Do not choose one global strategy for the whole PDF unless inspection proves one repeated layout. Treat the PDF like a small script made from puzzle pieces: make one independent extraction decision per visual table, grouped logical table, or coordinate/text region. Prefer `table_region_hints` first when present; each group is its own decision unit with its own `suggested_method`, pages, columns, row count, and detection refs. Use a detected-table strategy for one group and a field-value, line-value, or coordinate strategy for another whenever the evidence differs. Use each detection's `interpretation.rows` when `interpretation.usable` is true; they include repaired wrapped cells, code identifiers, and field/value classification. Use the selected 2x2 overview batches for layout selection and continuation checks, then load focused page images only when needed; use raw linear text only as a supplement for table names, exact spelling, punctuation, and wrapped values after a structured candidate exists. Ignore false positive detections instead of forcing them into tables.

If loaded skill instructions explicitly name repo-local companion or config files needed for the requested analysis, prepare those files in the same SQLite database too, even when they were not directly attached. Only use companion files that the loaded skill names with a concrete path or unambiguous filename; do not invent inputs.

Try to finish the extraction in the current prep_pdf stage run instead of saving extraction work for a later run.
Once `extract_pdf` succeeds with a usable shared SQLite result, stop using tools and return `status="prepared"`.
Do not continue inspecting after a successful extraction unless the extraction output is clearly unusable.

Do not invent source files, tables, pages, or outputs. If a loaded skill requires companion/config files and they cannot be found or prepared, stop and explain why instead of silently preparing only part of the required data.

Your structured response status must be one of:
- prepared: the extraction result is ready for downstream SQL work now.
- blocked: prep_pdf cannot proceed safely with the available tools or message clarity.
- error: prep_pdf failed in a non-retryable way.

Always include a short `summary` field in your structured response, even when the status is `prepared`.
""".strip()


def format_source_file_list(source_files: list[str]) -> str:
    """Render the source file list once for prompts."""
    return "\n".join(f"- {source_file}" for source_file in source_files) or "- (none)"


def summarize_skill_refs(skill_refs: list[dict[str, Any]]) -> str:
    """Render a compact summary of worker-visible skill references."""
    if not skill_refs:
        return ""

    lines = ["Skill refs available to prep_pdf:"]
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
    """Build the prep_pdf stage user message."""
    parts = [prompt.strip()] if prompt.strip() else []
    if max_prep_trials == 1:
        parts.append("prep_pdf stage run.")
    else:
        parts.append(f"prep_pdf trial {prep_attempt} of {max_prep_trials}.")
    parts.append(f"Source files:\n{format_source_file_list(source_files)}\nMessage: {message}")
    if worker_instructions.strip():
        parts.append(worker_instructions.strip())
    skill_ref_summary = summarize_skill_refs(skill_refs)
    if skill_ref_summary:
        parts.append(skill_ref_summary)
    if previous_attempts:
        parts.append("Previous prep_pdf trials:\n" + "\n".join(f"- {attempt}" for attempt in previous_attempts))
    if retry_instructions:
        parts.append("Retry instructions for this prep_pdf trial:\n" + "\n".join(f"- {instruction}" for instruction in retry_instructions))
    parts.append(
        "Return the data in the final extraction shape best suited for the message. Use tool observations instead of guessing, include explicitly named skill companion/config files when the skill requires them, and try to complete the extraction in this run."
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
