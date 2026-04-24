"""Workspace-skill context helpers for orchestrator prompts and workers."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from langchain_core.runnables import RunnableConfig

from ...tools import list_skills, load_skills, search_skills

SKILLS_PATH = "skills"
MAX_SKILL_REF_PREVIEW = 8


@dataclass
class WorkerSkillPayload:
    """Worker-facing skill instructions and loaded references for one run."""

    worker_instructions: str = ""
    skill_refs: list[dict[str, Any]] = field(default_factory=list)


def list_skills_context(
    *,
    path: str = SKILLS_PATH,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Return the raw workspace-skills listing payload."""
    return list_skills.invoke({"path": path}, config=config)


def search_skills_context(
    query: str,
    *,
    path: str = SKILLS_PATH,
    config: RunnableConfig | None = None,
) -> dict[str, Any]:
    """Return the raw semantic workspace-skills search payload."""
    return search_skills.invoke(
        {
            "path": path,
            "query": query,
        },
        config=config,
    )


def format_skills_overview(result: dict[str, Any]) -> str:
    """Render a deterministic system-prompt section for available skills."""
    if result.get("status") == "error":
        return "Workspace skills available under `skills`:\n- unavailable"

    diagnostics = [str(item) for item in result.get("diagnostics", [])]
    skills = list(result.get("skills", []))
    lines = ["Workspace skills available under `skills`:"]
    if skills:
        for skill in skills:
            skill_name = skill.get("name", "unknown")
            description = str(skill.get("description", "")).strip()
            skill_path = skill.get("path", "")
            lines.append(f"- {skill_name}: {description} ({skill_path})")
    else:
        lines.append("- none found")

    if diagnostics:
        lines.append(f"Diagnostics: {'; '.join(diagnostics[:3])}")
    return "\n".join(lines)


def format_skill_matches(result: dict[str, Any]) -> str:
    """Render a deterministic user-turn section for relevant skills."""
    if result.get("status") == "error":
        return "- unavailable"

    diagnostics = [str(item) for item in result.get("diagnostics", [])]
    skills = list(result.get("skills", []))
    lines = []
    if skills:
        for skill in skills:
            score = skill.get("score")
            suffix = f", score={score}" if score is not None else ""
            skill_name = skill.get("name", "unknown")
            description = str(skill.get("description", "")).strip()
            skill_path = skill.get("path", "")
            lines.append(f"- {skill_name}: {description} ({skill_path}{suffix})")
    else:
        lines.append("- none above threshold")

    if diagnostics:
        lines.append(f"Diagnostics: {'; '.join(diagnostics[:3])}")
    return "\n".join(lines)


def build_worker_skill_payload(
    task: str,
    *,
    path: str = SKILLS_PATH,
    config: RunnableConfig | None = None,
) -> WorkerSkillPayload:
    """Search and load matched skills into one worker-ready payload."""
    search_result = search_skills_context(task, path=path, config=config)
    matched_skills = list(search_result.get("skills", []))
    worker_sections: list[str] = []
    skill_refs: list[dict[str, Any]] = []
    for skill in matched_skills:
        skill_name = str(skill.get("name", "")).strip()
        if not skill_name:
            continue

        load_result = load_skills.invoke(
            {
                "path": path,
                "skills": skill_name,
            },
            config=config,
        )
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
        reference_count = len(skill_ref.get("references", [])) if isinstance(skill_ref.get("references"), list) else 0
        script_count = len(skill_ref.get("scripts", [])) if isinstance(skill_ref.get("scripts"), list) else 0
        summary_parts = [instruction_path] if instruction_path else []
        if reference_count:
            summary_parts.append(f"{reference_count} refs")
        if script_count:
            summary_parts.append(f"{script_count} scripts")
        lines.append(f"- {skill_name}: {', '.join(summary_parts) if summary_parts else 'loaded'}")
    if len(skill_refs) > MAX_SKILL_REF_PREVIEW:
        lines.append(f"- ... (+{len(skill_refs) - MAX_SKILL_REF_PREVIEW} more)")
    return "\n".join(lines)
