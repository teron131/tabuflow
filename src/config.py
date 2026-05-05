"""Repository-level configuration for local paths and environment overrides."""

from functools import lru_cache
import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
SKILLS_DIR = REPO_ROOT / "skills"
DEV_FRONTEND_ORIGINS = [
    "http://localhost:5174",
]
DEFAULT_AGENT_MODEL = "gpt-5.4-nano"
DEFAULT_REASONING_EFFORT = "high"
REQUIRED_LLM_ENV_VARS = ("LLM_API_KEY", "LLM_BASE_URL")
MISSING_LLM_CONFIG_MESSAGE = "LLM_API_KEY and LLM_BASE_URL are required for chat."


def configured_path(env_name: str, default: Path) -> Path:
    """Resolve a path from configuration while keeping a local default."""
    configured = os.environ.get(env_name)
    if not configured:
        return default
    return Path(configured).expanduser()


PREPARED_DATABASE_PATH = configured_path(
    "DATA_AGENTICS_PREPARED_DATABASE_PATH",
    REPO_ROOT / "data" / "tabular.sqlite",
)
UPLOADS_DIR = configured_path(
    "DATA_AGENTICS_UPLOADS_DIR",
    REPO_ROOT / "data" / "uploads",
)
WORKBENCH_SOURCE_ROOT = configured_path(
    "DATA_AGENTICS_WORKBENCH_SOURCE_ROOT",
    REPO_ROOT,
)


class AgentSettings(BaseSettings):
    """Environment-backed settings shared by application-owned agents."""

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


def has_llm_environment() -> bool:
    """Return whether model-backed features can be attempted."""
    return all(os.getenv(name) for name in REQUIRED_LLM_ENV_VARS)
