"""Simple structured-output validation agent package."""

from .state import ValidationInput, ValidationOutput, ValidationState
from .prompts import VALIDATION_SYSTEM_PROMPT
from .validation import ValidationAgent

__all__ = [
    "VALIDATION_SYSTEM_PROMPT",
    "ValidationAgent",
    "ValidationInput",
    "ValidationOutput",
    "ValidationState",
]
