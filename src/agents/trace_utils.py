"""Shared compact trace helpers for agent workflow artifacts."""

MAX_WORKFLOW_TRACE_MESSAGES = 24

SKILL_CONTEXT_STAGE = "skill_context"
PREP_STAGE = "prep"
SQL_STAGE = "sql"
VALIDATION_STAGE = "validation"
SAVE_STAGE = "save"


def trace_event(stage: str, message: str) -> str:
    """Return one stage-scoped trace message."""
    return f"{stage}: {message}"


def append_trace(trace: list[str], message: str) -> list[str]:
    """Append one trace message while keeping the trace compact."""
    return [*trace, message][-MAX_WORKFLOW_TRACE_MESSAGES:]


def append_stage_trace(trace: list[str], stage: str, message: str) -> list[str]:
    """Append one stage-scoped trace message while keeping the trace compact."""
    return append_trace(trace, trace_event(stage, message))


def append_trace_messages(trace: list[str], messages: list[str]) -> list[str]:
    """Append multiple trace messages while preserving the bounded trace shape."""
    next_trace = trace
    for message in messages:
        next_trace = append_trace(next_trace, message)
    return next_trace
