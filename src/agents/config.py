"""Shared settings and defaults for application-owned agents."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_AGENT_MODEL = "gpt-5.4-nano"
DEFAULT_REASONING_EFFORT = "high"


class AgentSettings(BaseSettings):
    """Environment-backed settings shared by orchestrator and worker agents."""

    main_llm: str | None = Field(default=None, validation_alias="MAIN_LLM")
    fast_llm: str | None = Field(default=None, validation_alias="FAST_LLM")
    quality_llm: str | None = Field(default=None, validation_alias="QUALITY_LLM")

    model_config = SettingsConfigDict(extra="ignore")


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    """Return the cached shared agent settings."""
    return AgentSettings()
