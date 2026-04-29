"""Multimodal LangChain message helpers."""

import base64
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

SUPPORTED_IMAGE_TYPES = {
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def image_data_block(path: Path) -> dict[str, Any]:
    """Read an image file as a LangChain standard base64 content block."""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_IMAGE_TYPES:
        supported = ", ".join(sorted(SUPPORTED_IMAGE_TYPES))
        raise ValueError(f"Unsupported image extension: {suffix}. Supported: {supported}")
    return {
        "type": "image",
        "source_type": "base64",
        "mime_type": SUPPORTED_IMAGE_TYPES[suffix],
        "data": base64.b64encode(path.read_bytes()).decode("utf-8"),
    }


class MediaMessage(HumanMessage):
    """HumanMessage carrying standard LangChain image data blocks."""

    def __init__(
        self,
        paths: str | Path | list[str | Path],
        description: str = "",
    ):
        items = [paths] if isinstance(paths, (str, Path)) else list(paths)
        content_blocks: list[dict[str, Any]] = []
        for item in items:
            content_blocks.append(image_data_block(Path(item)))
        if description:
            content_blocks.append({"type": "text", "text": description})
        super().__init__(content=content_blocks)
