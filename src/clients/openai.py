"""OpenAI-compatible client initialization and configuration."""

import os
from typing import Any, Literal

from dotenv import load_dotenv
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI as NativeChatOpenAI, OpenAIEmbeddings as NativeOpenAIEmbeddings

load_dotenv()

ReasoningEffort = Literal["minimal", "low", "medium", "high"]


def _resolve_api_key() -> str:
    """Resolve the OpenAI-compatible API key from the environment."""
    resolved_api_key = os.getenv("LLM_API_KEY")
    if not resolved_api_key:
        raise ValueError("LLM_API_KEY must be set")
    return resolved_api_key


def _resolve_base_url() -> str:
    """Resolve the OpenAI-compatible base URL from the environment."""
    resolved_base_url = os.getenv("LLM_BASE_URL")
    if not resolved_base_url:
        raise ValueError("LLM_BASE_URL must be set")
    return resolved_base_url


def _strip_reserved_model_kwargs(kwargs: dict[str, Any]) -> None:
    """Remove keys passed positionally in the constructor call."""
    kwargs.pop("model", None)
    kwargs.pop("temperature", None)


def ChatOpenAI(
    model: str,
    temperature: float = 0.7,
    reasoning_effort: ReasoningEffort | None = None,
    **kwargs: Any,
) -> BaseChatModel:
    """Initialize an OpenAI-compatible chat model.

    Args:
        model: Model name understood by the configured OpenAI-compatible endpoint.
        temperature: Sampling temperature.
        reasoning_effort: Optional reasoning effort passed through when supported.
        **kwargs: Additional arguments passed to ChatOpenAI.
    """
    resolved_api_key = _resolve_api_key()
    resolved_base_url = _resolve_base_url()
    _strip_reserved_model_kwargs(kwargs)

    model_kwargs: dict[str, Any] = {
        "api_key": resolved_api_key,
        "base_url": resolved_base_url,
        **kwargs,
    }
    if reasoning_effort:
        model_kwargs["reasoning_effort"] = reasoning_effort

    return NativeChatOpenAI(
        model=model,
        temperature=temperature,
        **model_kwargs,
    )


def OpenAIEmbeddings(
    model: str = "text-embedding-3-small",
    **kwargs: Any,
) -> Embeddings:
    """Initialize an OpenAI-compatible embedding model.

    Args:
        model: Embedding model name understood by the configured endpoint.
        **kwargs: Additional arguments passed to OpenAIEmbeddings.
    """
    resolved_api_key = _resolve_api_key()
    resolved_base_url = _resolve_base_url()

    return NativeOpenAIEmbeddings(
        model=model,
        api_key=resolved_api_key,
        base_url=resolved_base_url,
        **kwargs,
    )
