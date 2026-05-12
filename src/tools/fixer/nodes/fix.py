"""Fixer node that generates and applies hashline edit passes."""

from __future__ import annotations

import json
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from ....clients.openai import ChatOpenAI
from ...fs.hashline import HashlineEdit, HashlineEditResponse, HashlineReferenceError, edit_hashline
from ..prompts import (
    CLEAN_TASK_LOG,
    DEFAULT_FIXER_SYSTEM_PROMPT,
    build_fixer_agent_prompt,
    build_fixer_pass_prompt,
    build_hashline_repair_prompt,
    build_hashline_repair_system_prompt,
)
from ..state import FixerState
from .common import (
    EMPTY_EDIT_SENTINEL,
    _add_usage,
    _append_write_note,
    _build_runtime,
    _coerce_state,
    _continue_or_finalize,
    _FixerProgress,
    _FixerRuntime,
    _FixPassResult,
    _get_metadata,
    _WriteApplyResult,
    logger,
)


def _summarize_write_error(error: ValueError) -> str:
    """Extract a short single-line summary from an edit error."""
    first_line = str(error).splitlines()[0].strip()
    return first_line or error.__class__.__name__


def _build_edit_llm(state: FixerState):
    """Build the structured fixer model used for edit and repair passes."""
    return ChatOpenAI(
        model=state.fixer_model,
        temperature=0,
        reasoning_effort="low",
    ).with_structured_output(HashlineEditResponse, include_raw=True)


def _parse_edit_response(response: object) -> tuple[HashlineEditResponse, int, int, float]:
    """Parse a structured fixer response and extract usage metadata."""
    if isinstance(response, HashlineEditResponse):
        return response, 0, 0, 0.0
    if isinstance(response, BaseModel):
        return HashlineEditResponse.model_validate(response), 0, 0, 0.0
    if not isinstance(response, dict):
        return HashlineEditResponse.model_validate(response), 0, 0, 0.0

    if parsing_error := response.get("parsing_error"):
        raise ValueError(f"Could not parse fixer response: {parsing_error}")

    parsed = response.get("parsed")
    if parsed is None:
        raise ValueError("Fixer response did not include parsed structured output")
    raw = response.get("raw")
    tokens_in, tokens_out, cost = _get_metadata(raw) if isinstance(raw, AIMessage) else (0, 0, 0.0)
    return HashlineEditResponse.model_validate(parsed), tokens_in, tokens_out, cost


def _write_edits(
    *,
    runtime: _FixerRuntime,
    current_text: str,
    edits: list[HashlineEdit],
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost: float = 0.0,
) -> _WriteApplyResult:
    """Apply edits, write to disk, and preserve shared no-op handling."""
    updated_text = edit_hashline(current_text, edits)
    try:
        json.loads(current_text)
    except json.JSONDecodeError:
        pass
    else:
        try:
            json.loads(updated_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"write broke JSON validity: {exc}") from exc

    if updated_text == current_text:
        logger.info("[FIXER] Treating empty edit as no-op for %s", runtime.target_path)
        return _WriteApplyResult(
            after_text=current_text,
            write_error=EMPTY_EDIT_SENTINEL,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
        )

    runtime.fs.write_text(runtime.target_path, updated_text)
    return _WriteApplyResult(
        after_text=updated_text,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=cost,
    )


def _run_fix_pass(
    *,
    runtime: _FixerRuntime,
    state: FixerState,
    progress: _FixerProgress,
    current_text: str,
    turn: int,
) -> _FixPassResult:
    """Run one fixer model pass against the current file contents."""
    llm = _build_edit_llm(state)
    prompt = build_fixer_pass_prompt(
        target_file=state.target_file,
        current_text=runtime.fs.read_hashline(runtime.target_path),
        pass_number=turn,
        max_turns=state.max_iterations,
        task_log=progress.fixer_notes,
    )
    logger.info("[FIXER] Pass %s/%s chars=%s", turn, state.max_iterations, len(current_text))

    system_prompt = state.fixer_system_prompt or DEFAULT_FIXER_SYSTEM_PROMPT
    base_prompt = build_fixer_agent_prompt(
        target_file=state.target_file,
        fixer_context=state.fixer_context,
        max_turns=state.max_iterations,
    )
    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"{base_prompt}\n\n{prompt}"),
        ]
    )
    edit_response, tokens_in, tokens_out, cost = _parse_edit_response(response)
    return _FixPassResult(
        edits=edit_response.edits,
        raw_text=edit_response.model_dump_json(indent=2),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        cost=cost,
    )


def _run_hashline_edit_with_repair(
    *,
    runtime: _FixerRuntime,
    state: FixerState,
    progress: _FixerProgress,
    current_text: str,
    edits: list[HashlineEdit],
    attempted_text: str,
) -> _WriteApplyResult:
    """Apply hashline edits and try one repair pass if validation fails."""
    llm = _build_edit_llm(state)
    try:
        return _write_edits(runtime=runtime, current_text=current_text, edits=edits)
    except (HashlineReferenceError, ValueError) as error:
        logger.warning("[FIXER] Hashline edit rejected: %s", _summarize_write_error(error))
        logger.debug("[FIXER] Full hashline rejection details: %s", str(error).replace("\n", " | "))
        response = llm.invoke(
            [
                SystemMessage(
                    content=build_hashline_repair_system_prompt(
                        state.fixer_system_prompt or DEFAULT_FIXER_SYSTEM_PROMPT,
                    )
                ),
                HumanMessage(
                    content=build_hashline_repair_prompt(
                        error_text=str(error),
                        task_log=progress.fixer_notes or CLEAN_TASK_LOG,
                        current_text=runtime.fs.read_hashline(runtime.target_path),
                        attempted_edits=attempted_text,
                    )
                ),
            ]
        )
        repaired_response, tokens_in, tokens_out, cost = _parse_edit_response(response)
        if not repaired_response.edits:
            return _WriteApplyResult(
                after_text=None,
                write_error=str(error),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost=cost,
            )
        try:
            return _write_edits(
                runtime=runtime,
                current_text=current_text,
                edits=repaired_response.edits,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost=cost,
            )
        except (HashlineReferenceError, ValueError) as repaired_error:
            logger.warning("[FIXER] Repaired hashline edit rejected: %s", _summarize_write_error(repaired_error))
            logger.debug("[FIXER] Full repaired hashline rejection details: %s", str(repaired_error).replace("\n", " | "))
            return _WriteApplyResult(
                after_text=None,
                write_error=str(repaired_error),
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost=cost,
            )


def _handle_write_result(
    *,
    runtime: _FixerRuntime,
    state: FixerState,
    progress: _FixerProgress,
    current_text: str,
    turn: int,
    write_result: _WriteApplyResult,
) -> dict[str, object]:
    """Update fixer state after edit application, rollback, or no-op."""
    _add_usage(
        progress,
        tokens_in=write_result.tokens_in,
        tokens_out=write_result.tokens_out,
        cost=write_result.cost,
    )

    if write_result.write_error == EMPTY_EDIT_SENTINEL and write_result.after_text == current_text:
        return progress.state_update() | {
            "iteration": turn,
            "review_kind": "empty_edit",
            "fixer_last_text": "done",
        }

    if write_result.after_text is None:
        if write_result.write_error is not None:
            progress.fixer_notes = _append_write_note(progress.fixer_notes, write_result.write_error)
        return _continue_or_finalize(
            runtime=runtime,
            progress=progress,
            iteration=turn,
            restore_best_on_failure=state.restore_best_on_failure,
            max_iterations=state.max_iterations,
        )

    if write_result.after_text == current_text:
        logger.info("[FIXER] Edit pass %s made no changes; remaining work still logged", turn)
        return _continue_or_finalize(
            runtime=runtime,
            progress=progress,
            iteration=turn,
            restore_best_on_failure=state.restore_best_on_failure,
            max_iterations=state.max_iterations,
        )

    return progress.state_update() | {
        "iteration": turn,
        "review_kind": "patched",
        "fixer_last_text": state.fixer_last_text,
    }


def fix_node(state: FixerState | dict[str, Any]) -> dict[str, object]:
    """Run one fixer pass and queue the next review state."""
    state = _coerce_state(state)
    runtime = _build_runtime(state)
    progress = _FixerProgress.from_state(state)
    turn = state.iteration + 1

    if state.iteration == 0:
        logger.info(
            "[FIXER] Direct loop start file=%s model=%s max_turns=%s",
            state.target_file,
            state.fixer_model,
            state.max_iterations,
        )

    current_text = runtime.fs.read_text(runtime.target_path)
    pass_result = _run_fix_pass(
        runtime=runtime,
        state=state,
        progress=progress,
        current_text=current_text,
        turn=turn,
    )
    _add_usage(
        progress,
        tokens_in=pass_result.tokens_in,
        tokens_out=pass_result.tokens_out,
        cost=pass_result.cost,
    )
    if not pass_result.edits:
        return progress.state_update() | {
            "iteration": turn,
            "review_kind": "no_change",
            "fixer_last_text": "no_change",
        }

    write_result = _run_hashline_edit_with_repair(
        runtime=runtime,
        state=state,
        progress=progress,
        current_text=current_text,
        edits=pass_result.edits,
        attempted_text=pass_result.raw_text,
    )
    return _handle_write_result(
        runtime=runtime,
        state=state,
        progress=progress,
        current_text=current_text,
        turn=turn,
        write_result=write_result,
    )
