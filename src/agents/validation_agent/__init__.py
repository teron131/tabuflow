"""Simple structured-output validation agent package."""

from .state import ValidationInput, ValidationOutput
from .validation import VALIDATION_SYSTEM_PROMPT, ValidationAgent

__all__ = [
    "VALIDATION_SYSTEM_PROMPT",
    "ValidationAgent",
    "ValidationInput",
    "ValidationOutput",
]
