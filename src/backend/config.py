"""Repository-level configuration for local paths and environment overrides."""

from collections.abc import Mapping
from functools import lru_cache
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ..tabuflow.workspace_db import sqlite_database_path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
SKILLS_DIR = REPO_ROOT / "skills"
DEV_FRONTEND_ORIGINS = [
    "http://localhost:5174",
]
DEFAULT_AGENT_MODEL = "gpt-5.4-nano"
DEFAULT_REASONING_EFFORT = "high"
MISSING_LLM_CONFIG_MESSAGE = "LLM_API_KEY and LLM_BASE_URL are required for chat."

load_dotenv(ENV_FILE, override=False)


def configured_path(env_name: str, default: Path) -> Path:
    """Resolve a path from configuration while keeping a local default."""
    configured = os.environ.get(env_name)
    if not configured:
        return default
    return Path(configured).expanduser()


PREPARED_DATABASE_PATH = configured_path(
    "TABUFLOW_PREPARED_DATABASE_PATH",
    sqlite_database_path(root_dir=REPO_ROOT),
)
UPLOADS_DIR = configured_path(
    "TABUFLOW_UPLOADS_DIR",
    REPO_ROOT / "data" / "uploads",
)
WORKBENCH_SOURCE_ROOT = configured_path(
    "TABUFLOW_WORKBENCH_SOURCE_ROOT",
    REPO_ROOT,
)


class AgentSettings(BaseSettings):
    """Environment-backed settings shared by application-owned agents."""

    llm_model: str | None = Field(default=None, validation_alias="LLM_MODEL")
    llm_api_key: str | None = Field(default=None, validation_alias="LLM_API_KEY")
    llm_base_url: str | None = Field(default=None, validation_alias="LLM_BASE_URL")

    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore")

    @property
    def llm_configured(self) -> bool:
        """Return whether the OpenAI-compatible runtime can be used."""
        return bool(self.llm_api_key and self.llm_base_url)

    def resolve_model(self, model: str | None = None) -> str:
        """Return an explicit model override or the configured default agent model."""
        return model or self.llm_model or DEFAULT_AGENT_MODEL

    def llm_payload(self) -> dict[str, Any]:
        """Return browser-editable OpenAI-compatible LLM settings."""
        return {
            "model": self.resolve_model(),
            "api_key": self.llm_api_key or "",
            "base_url": self.llm_base_url or "",
            "configured": self.llm_configured,
        }


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    """Return the cached shared agent settings."""
    return AgentSettings()


def reload_agent_settings() -> AgentSettings:
    """Reload repo env values and return fresh agent settings."""
    load_dotenv(ENV_FILE, override=True)
    get_agent_settings.cache_clear()
    return get_agent_settings()


def resolve_agent_model(model: str | None = None) -> str:
    """Resolve the model name shared by application-owned agents."""
    return get_agent_settings().resolve_model(model)


def has_llm_environment() -> bool:
    """Return whether model-backed features can be attempted."""
    return get_agent_settings().llm_configured


def normalize_env_value(value: Any) -> str | None:
    """Return a stripped environment value or None when empty."""
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def write_env_file(updates: Mapping[str, str | None]) -> None:
    """Persist env updates while preserving unrelated lines."""
    if not ENV_FILE.exists():
        lines = [f"{key}={value}" for key, value in updates.items() if value is not None]
        ENV_FILE.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        return

    existing_lines = ENV_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    updated_lines: list[str] = []
    seen_keys: set[str] = set()
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            updated_lines.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key not in updates:
            updated_lines.append(line)
            continue
        value = updates[key]
        if value is not None:
            updated_lines.append(f"{key}={value}")
        seen_keys.add(key)

    for key, value in updates.items():
        if key not in seen_keys and value is not None:
            updated_lines.append(f"{key}={value}")

    ENV_FILE.write_text("\n".join(updated_lines) + "\n", encoding="utf-8")


def apply_env_updates(updates: Mapping[str, str | None]) -> None:
    """Apply env updates to the active process and refresh cached settings."""
    for key, value in updates.items():
        if value is None:
            os.environ.pop(key, None)
            continue
        os.environ[key] = value
    get_agent_settings.cache_clear()


def llm_settings_payload() -> dict[str, Any]:
    """Return the browser-editable OpenAI-compatible LLM settings."""
    return reload_agent_settings().llm_payload()


def update_llm_settings(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Persist and apply browser-submitted OpenAI-compatible LLM settings."""
    updates = {
        "LLM_MODEL": normalize_env_value(payload.get("model")),
        "LLM_API_KEY": normalize_env_value(payload.get("api_key")),
        "LLM_BASE_URL": normalize_env_value(payload.get("base_url")),
    }
    write_env_file(updates)
    apply_env_updates(updates)
    return llm_settings_payload()
