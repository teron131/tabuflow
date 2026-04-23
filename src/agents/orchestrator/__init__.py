"""Top-level user-facing orchestration agent."""

from .middleware import SkillsContextMiddleware
from .orchestrator import DEFAULT_MODEL, DEFAULT_MODEL_ENV, Orchestrator
from .prompts import ORCHESTRATOR_SYSTEM_PROMPT, build_system_prompt
from .state import OrchestratorState
from .tools import (
    format_skill_matches,
    format_skills_overview,
    list_skills_context,
    make_orchestrator_tools,
    search_skills_context,
)

__all__ = [
    "DEFAULT_MODEL",
    "DEFAULT_MODEL_ENV",
    "ORCHESTRATOR_SYSTEM_PROMPT",
    "Orchestrator",
    "OrchestratorState",
    "SkillsContextMiddleware",
    "build_system_prompt",
    "format_skill_matches",
    "format_skills_overview",
    "list_skills_context",
    "make_orchestrator_tools",
    "search_skills_context",
]
