"""Shared settings and defaults for application-owned agents."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_AGENT_MODEL = "openai/gpt-5.4-nano"
DEFAULT_REASONING_EFFORT = "high"


class AgentSettings(BaseSettings):
    """Environment-backed settings shared by orchestrator and worker agents."""

    main_llm: str | None = Field(default=None, validation_alias="MAIN_LLM")
    fast_llm: str | None = Field(default=None, validation_alias="FAST_LLM")
    quality_llm: str | None = Field(default=None, validation_alias="QUALITY_LLM")

    model_config = SettingsConfigDict(extra="ignore")

    def resolve_orchestrator_model(self) -> str:
        """Resolve the top-level orchestrator model name."""
        return self.main_llm or self.fast_llm or DEFAULT_AGENT_MODEL

    def resolve_worker_model(self, *, model: str | None = None) -> str:
        """Resolve the default worker model name for prep and validation."""
        return model or self.fast_llm or self.quality_llm or DEFAULT_AGENT_MODEL

    def resolve_sql_model(self, *, model: str | None = None) -> str:
        """Resolve the default SQL planner model name."""
        return model or self.fast_llm or DEFAULT_AGENT_MODEL


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    """Return the cached shared agent settings."""
    return AgentSettings()
