"""Shared fixer node runtime and progress helpers."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage

from ...fs.fs_tools import SandboxFS
from ..state import FixerState

logger = logging.getLogger(__name__)

EMPTY_EDIT_SENTINEL = "__FIXER_EMPTY_EDIT__"
MAX_REPEAT_REMAINING_REVIEWS = 2


@dataclass(frozen=True, slots=True)
class _FixerRuntime:
    fs: SandboxFS
    target_path: str
    root_path: Path


@dataclass(slots=True)
class _FixerProgress:
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_cost: float = 0.0
    fixer_notes: str = ""
    best_text: str | None = None
    best_notes: str = ""
    best_score: tuple[int, int] | None = None
    repeated_remaining_reviews: int = 0
    last_remaining_block: str = ""

    @classmethod
    def from_state(cls, state: FixerState) -> _FixerProgress:
        """Create mutable progress from persisted graph state."""
        return cls(
            total_tokens_in=state.fixer_tokens_in,
            total_tokens_out=state.fixer_tokens_out,
            total_cost=state.fixer_cost,
            fixer_notes=state.fixer_notes,
            best_text=state.best_text,
            best_notes=state.best_notes,
            best_score=state.best_score,
            repeated_remaining_reviews=state.repeated_remaining_reviews,
            last_remaining_block=state.last_remaining_block,
        )

    def build_result(self, *, iteration: int, completed: bool, last_text: str) -> dict[str, object]:
        """Build the public-facing fixer result fields."""
        return {
            "iteration": iteration,
            "fixer_tokens_in": self.total_tokens_in,
            "fixer_tokens_out": self.total_tokens_out,
            "fixer_cost": self.total_cost,
            "fixer_notes": self.fixer_notes,
            "fixer_completed": completed,
            "fixer_last_text": last_text,
        }

    def state_update(self) -> dict[str, object]:
        """Build the internal state fields that persist across turns."""
        return {
            "fixer_tokens_in": self.total_tokens_in,
            "fixer_tokens_out": self.total_tokens_out,
            "fixer_cost": self.total_cost,
            "fixer_notes": self.fixer_notes,
            "best_text": self.best_text,
            "best_notes": self.best_notes,
            "best_score": self.best_score,
            "repeated_remaining_reviews": self.repeated_remaining_reviews,
            "last_remaining_block": self.last_remaining_block,
        }


@dataclass(frozen=True, slots=True)
class _FixPassResult:
    edits: list
    raw_text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0


@dataclass(frozen=True, slots=True)
class _WriteApplyResult:
    after_text: str | None
    write_error: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0


def _coerce_state(state: FixerState | dict[str, Any]) -> FixerState:
    """Normalize LangGraph state input to the pydantic state model."""
    return state if isinstance(state, FixerState) else FixerState.model_validate(state)


def _get_metadata(ai_message: AIMessage) -> tuple[int, int, float]:
    """Return token usage and cost from common LangChain metadata shapes."""
    usage_metadata = getattr(ai_message, "usage_metadata", None)
    if isinstance(usage_metadata, dict) and usage_metadata:
        return (
            int(usage_metadata.get("input_tokens") or 0),
            int(usage_metadata.get("output_tokens") or 0),
            0.0,
        )

    response_metadata = getattr(ai_message, "response_metadata", None)
    if not isinstance(response_metadata, dict) or not response_metadata:
        return 0, 0, 0.0

    token_usage = response_metadata.get("token_usage")
    if isinstance(token_usage, dict) and token_usage:
        return (
            int(token_usage.get("prompt_tokens") or 0),
            int(token_usage.get("completion_tokens") or 0),
            float(token_usage.get("cost") or 0.0),
        )

    legacy_usage = response_metadata.get("usage_metadata")
    if isinstance(legacy_usage, dict) and legacy_usage:
        input_tokens = int(legacy_usage.get("prompt_token_count") or legacy_usage.get("input_token_count") or 0)
        output_tokens = int(legacy_usage.get("candidates_token_count") or legacy_usage.get("output_token_count") or 0)
        return input_tokens, output_tokens, 0.0

    return 0, 0, 0.0


def _append_write_note(existing_notes: str, note: str) -> str:
    """Append a write-related note to the accumulated fixer notes."""
    write_note = f"WRITE NOTE:\n- {note}"
    return f"{existing_notes}\n\n{write_note}".strip() if existing_notes else write_note


def _add_usage(
    progress: _FixerProgress,
    *,
    tokens_in: int,
    tokens_out: int,
    cost: float,
) -> None:
    """Accumulate token and cost usage into mutable progress."""
    progress.total_tokens_in += tokens_in
    progress.total_tokens_out += tokens_out
    progress.total_cost += cost


def _restore_best_snapshot(
    *,
    runtime: _FixerRuntime,
    progress: _FixerProgress,
) -> str:
    """Restore the best-known file snapshot and return a note about it."""
    if progress.best_text is None:
        return ""

    current_disk_text = runtime.fs.read_text(runtime.target_path)
    if current_disk_text != progress.best_text:
        runtime.fs.write_text(runtime.target_path, progress.best_text)
    return _append_write_note(progress.best_notes, "restored best snapshot after max_turns")


def _build_runtime(state: FixerState) -> _FixerRuntime:
    """Create filesystem runtime helpers for the current target file."""
    root_path = Path(state.root_dir)
    target_path = f"/{state.target_file.lstrip('/')}"
    return _FixerRuntime(
        fs=SandboxFS(root_dir=root_path),
        target_path=target_path,
        root_path=root_path,
    )


def _continue_or_finalize(
    *,
    runtime: _FixerRuntime,
    progress: _FixerProgress,
    iteration: int,
    restore_best_on_failure: bool,
    max_iterations: int,
) -> dict[str, object]:
    """Continue the loop or finalize when the turn budget is reached."""
    if iteration >= max_iterations:
        if restore_best_on_failure:
            progress.fixer_notes = _restore_best_snapshot(runtime=runtime, progress=progress) or progress.fixer_notes
        logger.warning("[FIXER] Stop reason=max_turns after passes=%s", max_iterations)
        return (
            progress.state_update()
            | progress.build_result(
                iteration=iteration,
                last_text="max_turns",
                completed=False,
            )
            | {"review_kind": ""}
        )
    return progress.state_update() | {
        "iteration": iteration,
        "review_kind": "",
    }
