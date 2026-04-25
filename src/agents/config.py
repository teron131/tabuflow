"""Shared settings and defaults for application-owned agents."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_AGENT_MODEL = "gpt-5.4-nano"
DEFAULT_REASONING_EFFORT = "high"


class AgentSettings(BaseSettings):
    """Environment-backed settings shared by orchestrator and worker agents."""

    llm_model: str | None = Field(default=None, validation_alias="LLM_MODEL")

    model_config = SettingsConfigDict(extra="ignore")

    def resolve_model(self, model: str | None = None) -> str:
        """Return an explicit model override or the configured default agent model."""
        return model or self.llm_model or DEFAULT_AGENT_MODEL


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    """Return the cached shared agent settings."""
    return AgentSettings()


def resolve_agent_model(model: str | None = None) -> str:
    """Resolve the model name shared by application-owned agents."""
    return get_agent_settings().resolve_model(model)
