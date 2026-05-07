"""Artifact naming pipeline for generated SQL files and saved views."""

from collections.abc import Callable
import re
import secrets
from typing import Any

from langchain_core.messages import HumanMessage
from pydantic import BaseModel, Field

ARTIFACT_WORD_COUNT = 2
ARTIFACT_SUFFIX_CHARS = 6
SLUG_PATTERN = re.compile(r"[a-z0-9]+")
SLUG_STOP_WORDS = {
    "a",
    "an",
    "and",
    "analysis",
    "artifact",
    "build",
    "by",
    "create",
    "current",
    "default",
    "for",
    "from",
    "get",
    "give",
    "in",
    "make",
    "of",
    "on",
    "please",
    "produce",
    "query",
    "result",
    "results",
    "show",
    "sql",
    "the",
    "to",
    "with",
}
SLUG_FALLBACK_WORDS = ("data", "query", "result")
ArtifactNamerFn = Callable[[str], str]


class SQLArtifactName(BaseModel):
    """Structured model output for one SQL artifact name."""

    words: list[str] = Field(
        min_length=ARTIFACT_WORD_COUNT,
        max_length=ARTIFACT_WORD_COUNT,
        description="Exactly two concrete noun words for the artifact name, without the random suffix.",
    )


def build_sql_artifact_namer(llm: Any) -> ArtifactNamerFn:
    """Build a structured-output namer from the shared orchestrator model."""
    structured_llm = llm.with_structured_output(SQLArtifactName)

    def namer(description: str) -> str:
        """Return the model-chosen two-word artifact stem."""
        response = structured_llm.invoke(
            [
                HumanMessage(
                    content=(
                        "Name this SQL artifact with exactly two concrete noun words. "
                        "Use lowercase words only. Do not include the suffix, file extension, verbs, or filler words. "
                        "The suffix may be a random run id or a stable content fingerprint, so choose words that describe the artifact content.\n\n"
                        f"Artifact context:\n{description}"
                    )
                )
            ]
        )
        artifact_name = response if isinstance(response, SQLArtifactName) else SQLArtifactName.model_validate(response)
        return "-".join(_slug_words(artifact_name.words))

    return namer


def name_sql_artifact(description: str, run_id: str | None) -> str:
    """Return a SQL artifact stem with two semantic words and six id chars."""
    slug_tokens = _slug_words(SLUG_PATTERN.findall(description.lower()))
    suffix = _artifact_suffix(run_id)
    return "-".join([*slug_tokens, suffix])


def _artifact_suffix(identifier: str | None) -> str:
    """Return a stable suffix from an id, or a random fallback when no id exists."""
    suffix = secrets.token_hex(ARTIFACT_SUFFIX_CHARS)[:ARTIFACT_SUFFIX_CHARS]
    if identifier and identifier != "default":
        stable_suffix = "".join(SLUG_PATTERN.findall(identifier.lower()))[:ARTIFACT_SUFFIX_CHARS]
        if stable_suffix:
            suffix = stable_suffix.ljust(ARTIFACT_SUFFIX_CHARS, "0")
    return suffix


def _slug_words(words: list[str]) -> list[str]:
    """Return exactly two safe semantic slug words."""
    semantic_tokens = [token for word in words for token in SLUG_PATTERN.findall(word.lower()) if token not in SLUG_STOP_WORDS]
    slug_tokens = semantic_tokens[:ARTIFACT_WORD_COUNT]
    for fallback_word in SLUG_FALLBACK_WORDS:
        if len(slug_tokens) >= ARTIFACT_WORD_COUNT:
            break
        if fallback_word not in slug_tokens:
            slug_tokens.append(fallback_word)
    return slug_tokens
