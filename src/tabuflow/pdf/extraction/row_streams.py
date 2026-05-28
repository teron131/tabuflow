"""Content rows plus non-content extraction provenance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ExtractedRows:
    """Represent extracted content rows and their source pages separately."""

    rows: list[dict[str, str]]
    source_pages: list[int]
    row_metadata: list[dict[str, Any]] = field(default_factory=list)
