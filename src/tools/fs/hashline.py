"""Minimal hashline edit tool.

Reference from oh-my-pi's hashline edit tool:
https://blog.can.ac/2026/02/12/the-harness-problem/
https://github.com/can1357/oh-my-pi/blob/b55dc0d107673ef9ff11559498aa67d4ebfa78be/packages/coding-agent/src/patch/hashline.ts
"""

from __future__ import annotations

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator

type HashlineOperation = Literal["replace_range", "insert_before", "insert_after"]
type _HashlineReplacement = tuple[int, int, list[str]]

_VISIBLE_HASH_LENGTH = 6
_HASH_DIGEST_SIZE = 4
_HASHLINE_REF_RE = re.compile(rf"^(?P<line>\d+)#(?P<hash>[0-9a-f]{{{_VISIBLE_HASH_LENGTH}}})$")
_WHITESPACE_RE = re.compile(r"\s+")
_HASHLINE_LINE_RE = re.compile(rf"^(?P<ref>\d+#[0-9a-f]{{{_VISIBLE_HASH_LENGTH}}}):(?P<content>.*)$")
_MISMATCH_PREVIEW_RADIUS = 1


class HashlineReferenceError(ValueError):
    """Raised when a hashline ref cannot be resolved against the current file text."""


class HashlineEdit(BaseModel):
    """Describe one hashline edit anchored to existing `LINE#HASH` references."""

    operation: HashlineOperation = Field(description="Edit operation.")
    start_ref: str = Field(description="Target hashline ref, for example '12#ab3f9d'.")
    end_ref: str | None = Field(default=None, description="Inclusive end ref for range replacement.")
    lines: list[str] = Field(default_factory=list, description="Replacement or inserted file lines without hashline prefixes.")

    @field_validator("end_ref")
    @classmethod
    def validate_end_ref(cls, value: str | None, info: ValidationInfo) -> str | None:
        """Validate the ending hashline reference for an edit."""
        if info.data.get("operation") == "replace_range" and not value:
            raise ValueError("replace_range requires end_ref")
        return value


class HashlineEditResponse(BaseModel):
    """Wrap the set of hashline edits produced for a single file rewrite pass."""

    edits: list[HashlineEdit] = Field(default_factory=list, description="Edit batch for the current file.")


def _compute_line_hash(line_number: int, line: str) -> str:
    """Return the visible hash fragment for a line at a specific line number.

    The hash ignores whitespace differences and includes the line number so a ref identifies both the content and its position in the file.
    """
    normalized_line = _WHITESPACE_RE.sub("", line.rstrip("\r"))
    data = f"{line_number}\0{normalized_line}".encode()
    digest = hashlib.blake2s(data=data, digest_size=_HASH_DIGEST_SIZE)
    return digest.hexdigest()[:_VISIBLE_HASH_LENGTH]


def _render_hashline_line(line_number: int, line: str) -> str:
    """Render one source line in the `LINE#HASH:content` prompt format."""
    line_hash = _compute_line_hash(line_number, line)
    return f"{line_number}#{line_hash}:{line}"


def format_hashline_text(text: str) -> str:
    """Convert plain text into hashline-formatted lines for model-facing prompts."""
    return "\n".join(_render_hashline_line(line_number, line) for line_number, line in enumerate(text.splitlines(), start=1))


def _build_error_message(
    *,
    ref: str,
    lines: list[str],
    line_number: int,
) -> str:
    """Build a helpful error message for an invalid or stale hashline ref."""

    previews: list[str] = [f"Stale hashline ref: {ref}"]
    if 1 <= line_number <= len(lines):
        previews.append(f"Current line at that position: {_render_hashline_line(line_number, lines[line_number - 1])}")
    else:
        previews.append(f"Current file has {len(lines)} lines.")

    preview_start = max(1, line_number - _MISMATCH_PREVIEW_RADIUS)
    preview_end = min(len(lines), line_number + _MISMATCH_PREVIEW_RADIUS)
    if preview_start <= preview_end:
        previews.append("Nearby current refs:")
        previews.extend(f"- {_render_hashline_line(index, lines[index - 1])}" for index in range(preview_start, preview_end + 1))
    return "\n".join(previews)


def _validate_ref(ref: str, lines: list[str]) -> int:
    """Resolve a hashline ref to its current 1-based line number or raise."""
    if not (match := _HASHLINE_REF_RE.fullmatch(ref.strip())):
        raise HashlineReferenceError(f"Invalid hashline ref: {ref!r}")

    line_number = int(match.group("line"))
    expected_hash = match.group("hash")
    if not 1 <= line_number <= len(lines):
        raise HashlineReferenceError(_build_error_message(ref=ref, lines=lines, line_number=line_number))

    if _compute_line_hash(line_number, lines[line_number - 1]) != expected_hash:
        raise HashlineReferenceError(_build_error_message(ref=ref, lines=lines, line_number=line_number))
    return line_number


def _edit_bounds(edit: HashlineEdit, lines: list[str]) -> tuple[int, int]:
    """Translate a hashline edit into the slice bounds used for list replacement."""
    start_line_number = _validate_ref(edit.start_ref, lines)
    if edit.operation != "replace_range":
        insert_index = start_line_number - 1 if edit.operation == "insert_before" else start_line_number
        return insert_index, insert_index

    end_line_number = _validate_ref(edit.end_ref or "", lines)
    if end_line_number < start_line_number:
        raise ValueError(f"replace_range end_ref must not be before start_ref: {edit.start_ref} -> {edit.end_ref}")
    return start_line_number - 1, end_line_number


def _normalize_replacements(edit: HashlineEdit) -> list[str]:
    """Normalize replacement lines by removing echoed refs from matching lines."""
    valid_refs = {ref for ref in (edit.start_ref, edit.end_ref) if ref}
    return [_strip_accidental_ref_prefix(line, valid_refs) for line in edit.lines]


def _strip_accidental_ref_prefix(line: str, valid_refs: set[str]) -> str:
    """Remove a leading `LINE#HASH:` prefix when it matches one of the edit refs."""
    if (match := _HASHLINE_LINE_RE.match(line)) and match.group("ref") in valid_refs:
        return match.group("content")
    return line


def edit_hashline(text: str, edits: list[HashlineEdit]) -> str:
    """Apply a validated batch of hashline edits to plain file text.

    The function resolves all target refs against the original text first, rejects overlapping replacement ranges, then applies the edits from bottom to top so earlier list replacements do not shift later slice indices.
    """
    if not edits:
        return text

    has_trailing_newline = text.endswith("\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    replacements: list[_HashlineReplacement] = []

    for edit in edits:
        start, end = _edit_bounds(edit, lines)
        replacements.append((start, end, _normalize_replacements(edit)))

    replacements.sort(key=lambda replacement: (replacement[0], replacement[1]))
    previous_end = -1
    for start, end, _ in replacements:
        if end <= start:
            continue
        if start < previous_end:
            raise ValueError("Hashline edits contain overlapping replace_range targets.")
        previous_end = end

    for start, end, new_lines in reversed(replacements):
        lines[start:end] = new_lines

    if not lines:
        return ""
    return "\n".join(lines) + ("\n" if has_trailing_newline else "")
