"""Workspace-skill context helpers for orchestrator prompts and workers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...config import SKILLS_DIR
from ...tools.skills import load_skill, search_skills

SKILLS_PATH = str(SKILLS_DIR)
MAX_SKILL_REF_PREVIEW = 8
MAX_TEXT_REFERENCE_PREVIEW = 4
MAX_TEXT_REFERENCE_CHARS = 8_000
MAX_SQL_REFERENCE_PREVIEW = 4
MAX_SQL_REFERENCE_CHARS = 12_000


@dataclass
class WorkerSkillPayload:
    """Worker-facing skill instructions and loaded references for one run."""

    worker_instructions: str = ""
    skill_refs: list[dict[str, Any]] = field(default_factory=list)


def format_skills_overview(result: dict[str, Any]) -> str:
    """Render a deterministic system-prompt section for available skills."""
    if result.get("status") == "error":
        return "Situational workspace skills available under `skills`:\n- unavailable"

    diagnostics = [str(item) for item in result.get("diagnostics", [])]
    skills = list(result.get("skills", []))
    lines = ["Situational workspace skills available under `skills`:"]
    if skills:
        for skill in skills:
            skill_name = skill.get("name", "unknown")
            description = str(skill.get("description", "")).strip()
            path = skill.get("path", "")
            lines.append(f"- {skill_name}: {description} ({path})")
    else:
        lines.append("- none found")

    if diagnostics:
        lines.append(f"Diagnostics: {'; '.join(diagnostics[:3])}")
    return "\n".join(lines)


def build_worker_skill_payload(
    message: str,
    *,
    path: str = SKILLS_PATH,
    config: object | None = None,
) -> WorkerSkillPayload:
    """Search and load matched skills into one worker-ready payload."""
    _ = config
    search_result = search_skills(path=path, query=message)
    matched_skills = list(search_result.get("skills", []))
    worker_sections: list[str] = []
    skill_refs: list[dict[str, Any]] = []
    for skill in matched_skills:
        skill_name = str(skill.get("name", "")).strip()
        if not skill_name:
            continue

        load_result = load_skill(path=path, skill=skill_name)
        loaded_skills = list(load_result.get("skills", []))
        if not loaded_skills:
            continue
        loaded_skill = loaded_skills[0]

        description = str(loaded_skill.get("description", "")).strip()
        instructions_payload = loaded_skill.get("instructions") or {}
        instructions = str(instructions_payload.get("content", "")).strip()
        if instructions:
            skill_title = f"Skill `{loaded_skill.get('name', 'unknown')}`: {description}"
            worker_sections.append(skill_title.strip())
            worker_sections.append(instructions)
        skill_refs.append(loaded_skill)

    return WorkerSkillPayload(
        worker_instructions="\n\n".join(section for section in worker_sections if section.strip()),
        skill_refs=skill_refs,
    )


def summarize_skill_refs(skill_refs: list[dict[str, Any]]) -> str:
    """Render a compact summary of worker-visible skill references."""
    if not skill_refs:
        return ""

    lines = ["Skill refs available to this run:"]
    for skill_ref in skill_refs[:MAX_SKILL_REF_PREVIEW]:
        skill_name = str(skill_ref.get("name", "unknown"))
        instructions = skill_ref.get("instructions") or {}
        instruction_path = str(instructions.get("relative_path") or skill_ref.get("path") or "")
        references = skill_ref.get("references", [])
        reference_count = len(references) if isinstance(references, list) else 0
        sql_reference_count = sum(1 for reference in references if str(reference.get("relative_path", "")).lower().endswith(".sql")) if isinstance(references, list) else 0
        script_count = len(skill_ref.get("scripts", [])) if isinstance(skill_ref.get("scripts"), list) else 0
        summary_parts = [instruction_path] if instruction_path else []
        if reference_count:
            summary_parts.append(f"{reference_count} refs")
        if sql_reference_count:
            summary_parts.append(f"{sql_reference_count} sql refs")
        if script_count:
            summary_parts.append(f"{script_count} scripts")
        lines.append(f"- {skill_name}: {', '.join(summary_parts) if summary_parts else 'loaded'}")
    if len(skill_refs) > MAX_SKILL_REF_PREVIEW:
        lines.append(f"- ... (+{len(skill_refs) - MAX_SKILL_REF_PREVIEW} more)")
    return "\n".join(lines)


def format_skill_references_for_sql(skill_refs: list[dict[str, Any]]) -> str:
    """Render loaded skill references for SQL planning context."""
    reference_sections: dict[str, list[str]] = {"text": [], "sql": []}
    for skill_ref in skill_refs:
        skill_name = str(skill_ref.get("name", "unknown"))
        references = skill_ref.get("references", [])
        if not isinstance(references, list):
            continue

        for reference in references:
            relative_path = str(reference.get("relative_path", ""))
            content = str(reference.get("content", "")).strip()
            if not content:
                continue

            reference_kind = str(reference.get("kind", ""))
            block_kind = "sql" if reference_kind == "sql" or relative_path.lower().endswith(".sql") else "text"
            max_sections = MAX_SQL_REFERENCE_PREVIEW if block_kind == "sql" else MAX_TEXT_REFERENCE_PREVIEW
            max_chars = MAX_SQL_REFERENCE_CHARS if block_kind == "sql" else MAX_TEXT_REFERENCE_CHARS
            if len(reference_sections[block_kind]) >= max_sections:
                continue
            if len(content) > max_chars:
                truncated_suffix = "\n-- truncated" if block_kind == "sql" else "\n\n<!-- truncated -->"
                content = content[:max_chars].rstrip() + truncated_suffix

            heading = "SQL reference" if block_kind == "sql" else "Reference"
            fence = "sql" if block_kind == "sql" else "markdown"
            reference_sections[block_kind].append(
                "\n".join(
                    [
                        f"{heading} from skill `{skill_name}` ({relative_path}):",
                        f"```{fence}",
                        content,
                        "```",
                    ]
                )
            )

    return "\n\n".join(
        [
            *reference_sections["text"],
            *reference_sections["sql"],
        ]
    )
