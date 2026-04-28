"""Chunk PDFs and use a vision LLM to OCR table content into CSV-ready rows."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from contextvars import copy_context
from dataclasses import dataclass, field
from itertools import pairwise
import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory
import time
from typing import Any

import httpx
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langsmith import traceable
from openai import APIConnectionError, BadRequestError
from pydantic import BaseModel, ConfigDict, Field
import pymupdf

from src.agents.config import resolve_agent_model
from src.clients.openai import ChatOpenAI
from src.tools.fixer import fix_file

DEFAULT_PAGES_PER_CHUNK = 3
DEFAULT_DPI = 192
DEFAULT_MAX_CONCURRENCY = 1
MAX_RETRIES = 3
IMAGE_MIME_TYPE = "image/jpeg"
BRIDGE_EDGE_TABLE_COUNT = 2
BRIDGE_EDGE_CROP_RATIO = 0.35

TABLE_OCR_SYSTEM_PROMPT = """You extract tables from PDF page images.

Use the page images as the source of truth. Use the PyMuPDF text reference only to confirm exact words, numbers, and punctuation.

Rules:
- Extract every visually tabular region in the provided pages.
- Preserve row order, column order, signs, currency symbols, commas, and decimals.
- If a table has no visible header, infer short neutral column names like "label" and "amount".
- Keep multi-line cell text in one cell using "\\n".
- Exclude repeated footers, page numbers, watermarks, and explanatory paragraphs outside tables.
- If there are no tables, return no table entries.
"""

TABLE_BRIDGE_FIX_SYSTEM_PROMPT = """Repair table extraction artifacts around an OCR chunk boundary.

Use the cropped boundary page images as the source of truth. Use the edge table reference only to confirm exact words, numbers, and punctuation.

Rules:
- Return the corrected table entries for this local boundary window only.
- Merge tables that were split across the chunk boundary when their columns and content clearly continue.
- Remove duplicated headers, repeated continuation rows, and duplicated partial tables caused by overlapping context.
- Preserve row order, column order, signs, currency symbols, commas, decimals, and cell text.
- Do not invent rows or columns beyond what the local boundary window supports.
- If the window already looks correct, return the same table entries.
"""

TABLE_BRIDGE_TEXT_FIX_SYSTEM_PROMPT = """Repair table extraction artifacts around an OCR chunk boundary.

You will receive the full edge-table window from adjacent chunks plus OCR evidence extracted only from cropped boundary page images.

Rules:
- Return the corrected full edge-table window, not only the cropped-image rows.
- Use the cropped boundary OCR evidence only to decide seam-local merges, duplicated headers, repeated continuation rows, and duplicated partial tables.
- Preserve full edge-table rows that are outside the cropped boundary unless they are clearly duplicated by the seam.
- Preserve row order, column order, signs, currency symbols, commas, decimals, cell text, and source page numbers.
- Do not invent rows or columns beyond what the edge tables and cropped boundary OCR support.
- If the window already looks correct, return the same table entries.
"""

TABLE_OVERALL_FIX_SYSTEM_PROMPT = """Repair full-document table extraction artifacts after chunk OCR and boundary repair.

You will receive the full ordered list of extracted tables.

Rules:
- Return the corrected table entries for the full document.
- Merge tables that were still split when their page order, title, columns, and content clearly continue.
- Remove duplicated tables, duplicated headers, repeated continuation rows, empty rows, and OCR-only artifacts.
- Preserve row order, column order, signs, currency symbols, commas, decimals, cell text, and source page numbers.
- Preserve table metadata when it is still accurate.
- Do not invent rows or columns beyond what the extracted tables support.
- If the full list already looks correct, return the same table entries.
"""


class OcrTable(BaseModel):
    """One table extracted from a PDF page chunk."""

    model_config = ConfigDict(extra="ignore")

    table_id: str | None = Field(default=None, description="Stable table identifier when present.")
    chunk_index: int | None = Field(default=None, description="Source chunk index when present.")
    table_index: int | None = Field(default=None, description="Table index within the source chunk when present.")
    title: str | None = Field(default=None, description="Nearby heading or section label when visible.")
    page_start: int | None = Field(default=None, description="First 1-based source page covered by this table.")
    page_end: int | None = Field(default=None, description="Last 1-based source page covered by this table.")
    columns: list[str] = Field(default_factory=list, description="Column names in visual order.")
    rows: list[list[str]] = Field(default_factory=list, description="Rows in visual order with one string per cell.")
    notes: str | None = Field(default=None, description="Short uncertainty note when needed.")


class TableOcrPayload(BaseModel):
    """Structured table OCR output for one PDF page chunk."""

    model_config = ConfigDict(extra="ignore")

    tables: list[OcrTable] = Field(default_factory=list, description="Tables detected in the provided page images.")


@dataclass(frozen=True, slots=True)
class PdfChunk:
    """Rendered page chunk plus PyMuPDF text reference."""

    index: int
    start_page: int
    end_page: int
    image_bytes: list[bytes]
    raw_text: str


@dataclass(frozen=True, slots=True)
class PdfBridgeImages:
    """Cropped visual context around one adjacent chunk boundary."""

    left_page: int
    right_page: int
    left_image: bytes
    right_image: bytes


@dataclass(frozen=True, slots=True)
class OcrUsage:
    """Token usage metadata reported by the model provider."""

    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0


@dataclass(frozen=True, slots=True)
class PdfTableOcrResult:
    """Paths and usage returned by the PDF table OCR workflow."""

    pdf_path: Path
    output_dir: Path
    sqlite_path: Path
    json_path: Path
    markdown_path: Path | None
    table_count: int
    row_count: int
    usage: OcrUsage = field(default_factory=OcrUsage)


def _trace_pdf_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Keep LangSmith PDF OCR pipeline inputs compact."""
    pdf_path = Path(inputs.get("pdf_path", ""))
    output_dir = inputs.get("output_dir")
    return {
        "pdf_path": str(pdf_path.expanduser()),
        "output_dir": None if output_dir is None else str(output_dir),
        "model": inputs.get("model"),
        "pages_per_chunk": inputs.get("pages_per_chunk"),
        "max_concurrency": inputs.get("max_concurrency"),
        "dpi": inputs.get("dpi"),
        "max_chunks": inputs.get("max_chunks"),
        "fix_bridges": inputs.get("fix_bridges"),
        "fix_overall": inputs.get("fix_overall"),
        "write_markdown": inputs.get("write_markdown"),
    }


def _trace_pdf_outputs(output: PdfTableOcrResult | None) -> dict[str, Any]:
    """Keep LangSmith PDF OCR pipeline outputs compact."""
    if output is None:
        return {}
    return {
        "pdf_path": str(output.pdf_path),
        "output_dir": str(output.output_dir),
        "sqlite_path": str(output.sqlite_path),
        "json_path": str(output.json_path),
        "markdown_path": None if output.markdown_path is None else str(output.markdown_path),
        "table_count": output.table_count,
        "row_count": output.row_count,
        "usage": {
            "input_tokens": output.usage.input_tokens,
            "output_tokens": output.usage.output_tokens,
            "cost": output.usage.cost,
        },
    }


@dataclass(frozen=True, slots=True)
class PdfOutputPaths:
    """Resolved output paths for one PDF OCR run."""

    output_dir: Path
    sqlite_path: Path
    json_path: Path
    markdown_path: Path | None


@dataclass(frozen=True, slots=True)
class BridgeTableWindow:
    """Adjacent chunk edge tables involved in one bridge repair."""

    left_prefix: list[dict[str, Any]]
    left_window: list[dict[str, Any]]
    right_window: list[dict[str, Any]]
    right_suffix: list[dict[str, Any]]


def _encode_image(data: bytes) -> str:
    """Return an image data URL for a vision request block."""
    encoded = base64.b64encode(data).decode("utf-8")
    return f"data:{IMAGE_MIME_TYPE};base64,{encoded}"


def _chunk_ranges(
    total_pages: int,
    pages_per_chunk: int,
) -> list[tuple[int, int]]:
    """Return 1-based inclusive page ranges."""
    return [(start, min(start + pages_per_chunk - 1, total_pages)) for start in range(1, total_pages + 1, pages_per_chunk)]


def _render_chunk(
    pdf_path: Path,
    *,
    chunk_index: int,
    start_page: int,
    end_page: int,
    dpi: int,
) -> PdfChunk:
    """Render one page range to JPEG bytes and raw text."""
    image_bytes: list[bytes] = []
    raw_text_parts: list[str] = []

    with pymupdf.open(str(pdf_path)) as document:
        for page_number in range(start_page, end_page + 1):
            page = document[page_number - 1]
            image_bytes.append(page.get_pixmap(dpi=dpi).tobytes("jpeg"))
            text = page.get_text().strip()
            if text:
                raw_text_parts.append(f"--- Page {page_number} text reference ---\n{text}")

    return PdfChunk(
        index=chunk_index,
        start_page=start_page,
        end_page=end_page,
        image_bytes=image_bytes,
        raw_text="\n\n".join(raw_text_parts),
    )


def _render_page_crop(
    pdf_path: Path,
    *,
    page_number: int,
    dpi: int,
    top: bool,
    ratio: float,
) -> bytes:
    """Render only the top or bottom slice of one 1-based PDF page."""
    if not 0 < ratio <= 1:
        raise ValueError("ratio must be > 0 and <= 1")

    with pymupdf.open(str(pdf_path)) as document:
        page = document[page_number - 1]
        rect: pymupdf.Rect = page.rect
        crop_height = rect.height * ratio
        clip = pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y0 + crop_height) if top else pymupdf.Rect(rect.x0, rect.y1 - crop_height, rect.x1, rect.y1)
        return page.get_pixmap(dpi=dpi, clip=clip).tobytes("jpeg")


def _render_bridge_images(
    pdf_path: Path,
    *,
    left_chunk: PdfChunk,
    right_chunk: PdfChunk,
    dpi: int,
    crop_ratio: float = BRIDGE_EDGE_CROP_RATIO,
) -> PdfBridgeImages:
    """Render narrow visual crops around one chunk boundary."""
    return PdfBridgeImages(
        left_page=left_chunk.end_page,
        right_page=right_chunk.start_page,
        left_image=_render_page_crop(pdf_path, page_number=left_chunk.end_page, dpi=dpi, top=False, ratio=crop_ratio),
        right_image=_render_page_crop(pdf_path, page_number=right_chunk.start_page, dpi=dpi, top=True, ratio=crop_ratio),
    )


def _text_block(text: str) -> dict[str, Any]:
    """Build an OpenAI-compatible text block."""
    return {"type": "text", "text": text}


def _image_block(data: bytes) -> dict[str, Any]:
    """Build an OpenAI-compatible image block."""
    return {"type": "image_url", "image_url": {"url": _encode_image(data)}}


def _usage_from_message(response: AIMessage) -> OcrUsage:
    """Read token usage from common LangChain/OpenAI-compatible metadata shapes."""
    usage_metadata = getattr(response, "usage_metadata", None)
    if isinstance(usage_metadata, dict) and usage_metadata:
        return OcrUsage(
            input_tokens=int(usage_metadata.get("input_tokens") or 0),
            output_tokens=int(usage_metadata.get("output_tokens") or 0),
        )

    response_metadata = getattr(response, "response_metadata", None)
    if not isinstance(response_metadata, dict):
        return OcrUsage()

    token_usage = response_metadata.get("token_usage")
    if isinstance(token_usage, dict) and token_usage:
        return OcrUsage(
            input_tokens=int(token_usage.get("prompt_tokens") or 0),
            output_tokens=int(token_usage.get("completion_tokens") or 0),
            cost=float(token_usage.get("cost") or 0.0),
        )
    return OcrUsage()


def _build_chunk_messages(chunk: PdfChunk) -> list[SystemMessage | HumanMessage]:
    """Build the vision prompt for a rendered PDF chunk."""
    blocks: list[dict[str, Any]] = [
        _text_block(f"Extract tables from PDF pages {chunk.start_page}-{chunk.end_page}."),
    ]
    for offset, image in enumerate(chunk.image_bytes):
        blocks.append(_text_block(f"Page {chunk.start_page + offset}:"))
        blocks.append(_image_block(image))
    blocks.append(_text_block(f"PyMuPDF text reference:\n{chunk.raw_text}"))

    return [
        SystemMessage(content=TABLE_OCR_SYSTEM_PROMPT),
        HumanMessage(content=blocks),
    ]


def _build_bridge_messages(
    *,
    left_chunk: PdfChunk,
    right_chunk: PdfChunk,
    bridge_images: PdfBridgeImages,
    left_tables: list[dict[str, Any]],
    right_tables: list[dict[str, Any]],
) -> list[SystemMessage | HumanMessage]:
    """Build the vision prompt for a cropped chunk-boundary repair."""
    bridge_payload = _build_bridge_payload(
        left_chunk=left_chunk,
        right_chunk=right_chunk,
        left_tables=left_tables,
        right_tables=right_tables,
    )
    blocks = [
        _text_block(
            f"Repair only the table boundary between chunk {left_chunk.index} page {bridge_images.left_page} and chunk {right_chunk.index} page {bridge_images.right_page}."
        ),
        _text_block(f"Bottom crop of page {bridge_images.left_page}:"),
        _image_block(bridge_images.left_image),
        _text_block(f"Top crop of page {bridge_images.right_page}:"),
        _image_block(bridge_images.right_image),
        _text_block(f"Edge table reference:\n{json.dumps(bridge_payload, indent=2, ensure_ascii=False)}"),
    ]
    return [
        SystemMessage(content=TABLE_BRIDGE_FIX_SYSTEM_PROMPT),
        HumanMessage(content=blocks),
    ]


def _parse_structured_response(response: Any) -> tuple[TableOcrPayload, OcrUsage]:
    """Parse a LangChain structured-output response and keep raw usage metadata."""
    if not isinstance(response, dict):
        payload = TableOcrPayload.model_validate(response)
        return payload, OcrUsage()

    if parsing_error := response.get("parsing_error"):
        raise ValueError(f"Could not parse OCR table response: {parsing_error}")

    parsed = response.get("parsed")
    if parsed is None:
        raise ValueError("OCR table response did not include parsed structured output")
    payload = parsed if isinstance(parsed, TableOcrPayload) else TableOcrPayload.model_validate(parsed)

    raw = response.get("raw")
    usage = _usage_from_message(raw) if isinstance(raw, AIMessage) else OcrUsage()
    return payload, usage


def _run_chunk_ocr(
    chunk: PdfChunk,
    *,
    model: str,
    config: RunnableConfig | None = None,
) -> tuple[TableOcrPayload, OcrUsage]:
    """Invoke the OCR model for one PDF chunk with retries."""
    llm = ChatOpenAI(
        model=model,
        temperature=0,
        reasoning_effort="medium",
    ).with_structured_output(TableOcrPayload, include_raw=True)
    messages = _build_chunk_messages(chunk)

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = llm.invoke(
                messages,
                config=config,
            )
            return _parse_structured_response(response)
        except (APIConnectionError, httpx.ReadError, httpx.RemoteProtocolError):
            if attempt == MAX_RETRIES:
                raise
            time.sleep(0.5 * attempt)
        except BadRequestError as exc:
            raise RuntimeError(f"OCR request failed for model {model!r} on chunk {chunk.index}: {exc}") from exc

    raise RuntimeError(f"Chunk {chunk.index} exhausted retries")


def _run_bridge_ocr_fix(
    *,
    left_chunk: PdfChunk,
    right_chunk: PdfChunk,
    bridge_images: PdfBridgeImages,
    left_tables: list[dict[str, Any]],
    right_tables: list[dict[str, Any]],
    model: str,
    config: RunnableConfig | None = None,
) -> tuple[TableOcrPayload, OcrUsage]:
    """Invoke the OCR model for one cropped chunk-boundary repair."""
    llm = ChatOpenAI(
        model=model,
        temperature=0,
        reasoning_effort="medium",
    ).with_structured_output(TableOcrPayload, include_raw=True)
    messages = _build_bridge_messages(
        left_chunk=left_chunk,
        right_chunk=right_chunk,
        bridge_images=bridge_images,
        left_tables=left_tables,
        right_tables=right_tables,
    )

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = llm.invoke(messages, config=config)
            return _parse_structured_response(response)
        except (APIConnectionError, httpx.ReadError, httpx.RemoteProtocolError):
            if attempt == MAX_RETRIES:
                raise
            time.sleep(0.5 * attempt)
        except BadRequestError as exc:
            raise RuntimeError(f"Bridge OCR request failed for model {model!r} across chunks {left_chunk.index}-{right_chunk.index}: {exc}") from exc

    raise RuntimeError(f"Bridge {left_chunk.index}-{right_chunk.index} exhausted retries")


def _normalize_row(
    row: Any,
    columns: list[str],
) -> list[str]:
    """Normalize one model row to a list of string cell values."""
    if isinstance(row, dict):
        return [str(row.get(column, "") or "") for column in columns]
    if isinstance(row, list):
        return [str(cell or "") for cell in row]
    return [str(row or "")]


def _normalize_table(
    table: Any,
    *,
    chunk_index: int,
    table_index: int,
    table_id: str,
    default_page_start: int,
    default_page_end: int,
) -> dict[str, Any] | None:
    """Normalize one model table payload to the persisted table shape."""
    raw_table = table.model_dump() if isinstance(table, BaseModel) else table
    if not isinstance(raw_table, dict):
        return None

    raw_rows = raw_table.get("rows")
    if not isinstance(raw_rows, list) or not raw_rows:
        return None

    raw_columns = raw_table.get("columns")
    columns = [str(column or "").strip() for column in raw_columns] if isinstance(raw_columns, list) else []
    rows = [_normalize_row(row, columns) for row in raw_rows]
    max_width = max([len(columns), *(len(row) for row in rows)], default=0)
    if not columns:
        columns = [f"column_{idx}" for idx in range(1, max_width + 1)]
    elif len(columns) < max_width:
        columns.extend(f"column_{idx}" for idx in range(len(columns) + 1, max_width + 1))

    padded_rows = [row + [""] * (len(columns) - len(row)) for row in rows]
    page_start = int(raw_table.get("page_start") or default_page_start)
    page_end = int(raw_table.get("page_end") or default_page_end)
    return {
        "chunk_index": int(raw_table.get("chunk_index") or chunk_index),
        "table_index": int(raw_table.get("table_index") or table_index),
        "table_id": str(raw_table.get("table_id") or table_id),
        "title": raw_table.get("title") or "",
        "page_start": page_start,
        "page_end": page_end,
        "columns": columns,
        "rows": padded_rows,
        "notes": raw_table.get("notes") or "",
    }


def _normalize_chunk_tables(
    payload: TableOcrPayload,
    *,
    chunk: PdfChunk,
) -> list[dict[str, Any]]:
    """Normalize all tables from one chunk OCR response."""
    tables: list[dict[str, Any]] = []
    for table_index, table in enumerate(payload.tables, start=1):
        normalized = _normalize_table(
            table,
            chunk_index=chunk.index,
            table_index=table_index,
            table_id=f"chunk_{chunk.index}_table_{table_index}",
            default_page_start=chunk.start_page,
            default_page_end=chunk.end_page,
        )
        if normalized is not None:
            tables.append(normalized)
    return tables


def _normalize_bridge_tables(
    payload: TableOcrPayload,
    *,
    left_chunk: PdfChunk,
    right_chunk: PdfChunk,
) -> list[dict[str, Any]]:
    """Normalize bridge-fixer tables into the persisted table shape."""
    bridge_index = left_chunk.index
    tables: list[dict[str, Any]] = []
    for table_index, table in enumerate(payload.tables, start=1):
        normalized = _normalize_table(
            table,
            chunk_index=bridge_index,
            table_index=table_index,
            table_id=f"bridge_{left_chunk.index}_{right_chunk.index}_table_{table_index}",
            default_page_start=left_chunk.start_page,
            default_page_end=right_chunk.end_page,
        )
        if normalized is not None:
            tables.append(normalized)
    return tables


def _normalize_overall_tables(payload: TableOcrPayload) -> list[dict[str, Any]]:
    """Normalize overall fixer tables into the persisted table shape."""
    tables: list[dict[str, Any]] = []
    for table_index, table in enumerate(payload.tables, start=1):
        normalized = _normalize_table(
            table,
            chunk_index=0,
            table_index=table_index,
            table_id=f"table_{table_index}",
            default_page_start=0,
            default_page_end=0,
        )
        if normalized is not None:
            tables.append(normalized)
    return tables


def _ordered_tables_from_chunks(
    chunks: list[PdfChunk],
    tables_by_chunk: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Return chunk tables in source page order."""
    return [table for chunk in chunks for table in tables_by_chunk.get(chunk.index, [])]


def _merge_usage(usages: list[OcrUsage]) -> OcrUsage:
    """Merge token usage metadata."""
    return OcrUsage(
        input_tokens=sum(usage.input_tokens for usage in usages),
        output_tokens=sum(usage.output_tokens for usage in usages),
        cost=sum(usage.cost for usage in usages),
    )


def _table_window_for_prompt(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only model-useful table fields for a bridge-fixer prompt."""
    return [
        {
            "table_id": table["table_id"],
            "chunk_index": table["chunk_index"],
            "table_index": table["table_index"],
            "title": table["title"],
            "page_start": table["page_start"],
            "page_end": table["page_end"],
            "columns": table["columns"],
            "rows": table["rows"],
            "notes": table["notes"],
        }
        for table in tables
    ]


def _build_bridge_payload(
    *,
    left_chunk: PdfChunk,
    right_chunk: PdfChunk,
    left_tables: list[dict[str, Any]],
    right_tables: list[dict[str, Any]],
    visual_boundary_tables: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the local bridge payload for adjacent chunk edge tables."""
    return {
        "previous_chunk": {
            "chunk_index": left_chunk.index,
            "page_start": left_chunk.start_page,
            "page_end": left_chunk.end_page,
            "edge_tables": _table_window_for_prompt(left_tables),
        },
        "next_chunk": {
            "chunk_index": right_chunk.index,
            "page_start": right_chunk.start_page,
            "page_end": right_chunk.end_page,
            "edge_tables": _table_window_for_prompt(right_tables),
        },
        "visual_boundary_tables": visual_boundary_tables or [],
        "tables": _table_window_for_prompt(left_tables + right_tables),
    }


def _build_overall_payload(tables: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the full-document payload for overall table cleanup."""
    return {"tables": _table_window_for_prompt(tables)}


def _usage_from_fixer_result(result: dict[str, object]) -> OcrUsage:
    """Convert generic fixer usage to OCR usage."""
    return OcrUsage(
        input_tokens=int(result.get("fixer_tokens_in") or 0),
        output_tokens=int(result.get("fixer_tokens_out") or 0),
        cost=float(result.get("fixer_cost") or 0.0),
    )


def _run_bridge_text_fix(
    *,
    left_chunk: PdfChunk,
    right_chunk: PdfChunk,
    left_tables: list[dict[str, Any]],
    right_tables: list[dict[str, Any]],
    visual_boundary_tables: list[dict[str, Any]],
    model: str,
    config: RunnableConfig | None = None,
) -> tuple[TableOcrPayload, OcrUsage]:
    """Repair one adjacent chunk boundary using seam OCR as evidence."""
    bridge_text = json.dumps(
        _build_bridge_payload(
            left_chunk=left_chunk,
            right_chunk=right_chunk,
            left_tables=left_tables,
            right_tables=right_tables,
            visual_boundary_tables=visual_boundary_tables,
        ),
        indent=2,
        ensure_ascii=False,
    )
    with TemporaryDirectory(prefix="pdf_table_bridge_") as bridge_dir:
        bridge_path = Path(bridge_dir) / f"bridge_{left_chunk.index}_{right_chunk.index}.json"
        bridge_path.write_text(f"{bridge_text}\n", encoding="utf-8")
        fixer_result = fix_file(
            path=bridge_path,
            fixer_model=model,
            fixer_context=TABLE_BRIDGE_TEXT_FIX_SYSTEM_PROMPT,
            max_iterations=2,
            config=config,
        )
        payload = json.loads(bridge_path.read_text(encoding="utf-8"))
    return TableOcrPayload.model_validate(payload), _usage_from_fixer_result(fixer_result)


def _run_overall_fix(
    tables: list[dict[str, Any]],
    *,
    model: str,
    config: RunnableConfig | None = None,
) -> tuple[TableOcrPayload, OcrUsage]:
    """Repair full-document table artifacts using the generic file fixer."""
    overall_text = json.dumps(_build_overall_payload(tables), indent=2, ensure_ascii=False)
    with TemporaryDirectory(prefix="pdf_table_overall_") as overall_dir:
        overall_path = Path(overall_dir) / "tables.json"
        overall_path.write_text(f"{overall_text}\n", encoding="utf-8")
        fixer_result = fix_file(
            path=overall_path,
            fixer_model=model,
            fixer_context=TABLE_OVERALL_FIX_SYSTEM_PROMPT,
            max_iterations=2,
            config=config,
        )
        payload = json.loads(overall_path.read_text(encoding="utf-8"))
    return TableOcrPayload.model_validate(payload), _usage_from_fixer_result(fixer_result)


def _bridge_window(
    *,
    ordered_tables: list[dict[str, Any]],
    right_tables: list[dict[str, Any]],
    edge_table_count: int,
) -> BridgeTableWindow | None:
    """Return adjacent edge-table slices for one bridge repair."""
    left_count = min(edge_table_count, len(ordered_tables))
    right_count = min(edge_table_count, len(right_tables))
    if not left_count or not right_count:
        return None
    return BridgeTableWindow(
        left_prefix=ordered_tables[:-left_count],
        left_window=ordered_tables[-left_count:],
        right_window=right_tables[:right_count],
        right_suffix=right_tables[right_count:],
    )


def _repair_bridge_window(
    *,
    pdf_path: Path,
    left_chunk: PdfChunk,
    right_chunk: PdfChunk,
    window: BridgeTableWindow,
    model: str,
    dpi: int,
    config: RunnableConfig | None = None,
) -> tuple[list[dict[str, Any]], OcrUsage]:
    """Repair one adjacent chunk edge-table window."""
    bridge_images = _render_bridge_images(
        pdf_path,
        left_chunk=left_chunk,
        right_chunk=right_chunk,
        dpi=dpi,
    )
    visual_payload, visual_usage = _run_bridge_ocr_fix(
        left_chunk=left_chunk,
        right_chunk=right_chunk,
        bridge_images=bridge_images,
        left_tables=window.left_window,
        right_tables=window.right_window,
        model=model,
        config=config,
    )
    visual_boundary_tables = [table.model_dump(exclude_none=True) if isinstance(table, BaseModel) else table for table in visual_payload.tables]
    payload, text_usage = _run_bridge_text_fix(
        left_chunk=left_chunk,
        right_chunk=right_chunk,
        left_tables=window.left_window,
        right_tables=window.right_window,
        visual_boundary_tables=visual_boundary_tables,
        model=model,
        config=config,
    )
    replacement = _normalize_bridge_tables(payload, left_chunk=left_chunk, right_chunk=right_chunk)
    if not replacement:
        replacement = window.left_window + window.right_window
    return replacement, _merge_usage([visual_usage, text_usage])


def _fix_table_bridges(
    pdf_path: Path,
    chunks: list[PdfChunk],
    tables_by_chunk: dict[int, list[dict[str, Any]]],
    *,
    model: str,
    dpi: int,
    edge_table_count: int = BRIDGE_EDGE_TABLE_COUNT,
    config: RunnableConfig | None = None,
) -> tuple[list[dict[str, Any]], OcrUsage]:
    """Run local visual table bridge fixes across adjacent chunk boundaries."""
    if len(chunks) < 2:
        return _ordered_tables_from_chunks(chunks, tables_by_chunk), OcrUsage()

    ordered_tables: list[dict[str, Any]] = list(tables_by_chunk.get(chunks[0].index, []))
    usages: list[OcrUsage] = []

    for left_chunk, right_chunk in pairwise(chunks):
        right_tables = list(tables_by_chunk.get(right_chunk.index, []))
        window = _bridge_window(
            ordered_tables=ordered_tables,
            right_tables=right_tables,
            edge_table_count=edge_table_count,
        )
        if window is None:
            ordered_tables.extend(right_tables)
            continue

        replacement, usage = _repair_bridge_window(
            pdf_path=pdf_path,
            left_chunk=left_chunk,
            right_chunk=right_chunk,
            window=window,
            model=model,
            dpi=dpi,
            config=config,
        )
        usages.append(usage)
        ordered_tables = window.left_prefix + replacement + window.right_suffix

    return ordered_tables, _merge_usage(usages)


def _fix_overall_tables(
    tables: list[dict[str, Any]],
    *,
    model: str,
    config: RunnableConfig | None = None,
) -> tuple[list[dict[str, Any]], OcrUsage]:
    """Run a final full-document table cleanup pass."""
    if not tables:
        return tables, OcrUsage()

    payload, usage = _run_overall_fix(tables, model=model, config=config)
    replacement = _normalize_overall_tables(payload)
    if not replacement:
        return tables, usage
    return replacement, usage


def _resolve_ocr_model(model: str | None) -> str:
    """Resolve the OCR model through the repo-wide LLM_MODEL setting."""
    return resolve_agent_model(model)


def _raw_table_payload(tables: list[dict[str, Any]]) -> dict[str, Any]:
    """Build the raw-data JSON payload without extraction metadata."""
    return {
        "tables": [
            {
                "title": table["title"] or None,
                "columns": table["columns"],
                "rows": table["rows"],
            }
            for table in tables
        ]
    }


def _write_json(
    path: Path,
    tables: list[dict[str, Any]],
) -> None:
    """Write extracted tables as one raw-data JSON file."""
    path.write_text(json.dumps(_raw_table_payload(tables), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sqlite_identifier(
    value: str,
    *,
    fallback: str,
) -> str:
    """Return a predictable SQLite identifier."""
    identifier = re.sub(r"[^0-9a-zA-Z]+", "_", value).strip("_").lower()
    if not identifier:
        identifier = fallback
    if identifier[0].isdigit():
        identifier = f"_{identifier}"
    return identifier


def _dedupe_identifier(
    identifier: str,
    seen: set[str],
) -> str:
    """Return a unique identifier within one SQLite namespace."""
    candidate = identifier
    suffix = 2
    while candidate in seen:
        candidate = f"{identifier}_{suffix}"
        suffix += 1
    seen.add(candidate)
    return candidate


def _quote_identifier(identifier: str) -> str:
    """Quote one SQLite identifier."""
    return '"' + identifier.replace('"', '""') + '"'


def _sqlite_table_names(tables: list[dict[str, Any]]) -> list[str]:
    """Return unique SQLite table names for extracted tables."""
    seen: set[str] = set()
    names: list[str] = []
    for index, table in enumerate(tables, start=1):
        base = _sqlite_identifier(str(table.get("title") or ""), fallback=f"table_{index:03d}")
        names.append(_dedupe_identifier(base, seen))
    return names


def _sqlite_columns(table: dict[str, Any]) -> list[str]:
    """Return unique SQLite column names for one extracted table."""
    seen: set[str] = set()
    columns: list[str] = []
    for index, column in enumerate(table["columns"], start=1):
        base = _sqlite_identifier(str(column or ""), fallback=f"column_{index}")
        columns.append(_dedupe_identifier(base, seen))
    return columns or ["value"]


def _write_sqlite(
    path: Path,
    tables: list[dict[str, Any]],
) -> None:
    """Write extracted tables into a SQLite database."""
    if path.exists():
        path.unlink()

    table_names = _sqlite_table_names(tables)
    with sqlite3.connect(path) as connection:
        for table_name, table in zip(table_names, tables, strict=True):
            columns = _sqlite_columns(table)
            column_sql = ", ".join(f"{_quote_identifier(column)} TEXT" for column in columns)
            connection.execute(f"CREATE TABLE {_quote_identifier(table_name)} ({column_sql})")

            placeholders = ", ".join("?" for _ in columns)
            insert_sql = f"INSERT INTO {_quote_identifier(table_name)} VALUES ({placeholders})"
            rows = [row[: len(columns)] + [""] * max(0, len(columns) - len(row)) for row in table["rows"]]
            connection.executemany(insert_sql, rows)


def _remove_stale_csv_outputs(
    output_dir: Path,
    *,
    pdf_stem: str,
) -> None:
    """Remove stale CSV artifacts from earlier output modes."""
    stale_combined_csv_path = output_dir / f"{pdf_stem}_llm_tables.csv"
    if stale_combined_csv_path.exists():
        stale_combined_csv_path.unlink()

    stale_table_dir = output_dir / f"{pdf_stem}_tables"
    if not stale_table_dir.exists():
        return
    for stale_path in stale_table_dir.glob("*.csv"):
        stale_path.unlink()
    with suppress(OSError):
        stale_table_dir.rmdir()


def _markdown_cell(value: Any) -> str:
    """Escape one table cell for Markdown output."""
    text = str(value or "").replace("\n", "<br>")
    return text.replace("|", "\\|")


def _write_markdown(
    path: Path,
    *,
    pdf_path: Path,
    tables: list[dict[str, Any]],
) -> None:
    """Write OCR tables as a raw Markdown preview."""
    lines = [f"# {pdf_path.name}", ""]

    for index, table in enumerate(tables, start=1):
        title = table["title"] or f"Table {index}"
        lines.extend(
            [
                f"## {title}",
                "",
            ]
        )

        columns = [_markdown_cell(column) for column in table["columns"]]
        lines.append("| " + " | ".join(columns) + " |")
        lines.append("| " + " | ".join("---" for _ in columns) + " |")
        for row in table["rows"]:
            cells = [_markdown_cell(cell) for cell in row]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def _validate_ocr_options(
    *,
    pages_per_chunk: int,
    max_concurrency: int,
) -> None:
    """Validate extraction options before any PDF or model work starts."""
    if pages_per_chunk < 1:
        raise ValueError("pages_per_chunk must be >= 1")
    if max_concurrency < 1:
        raise ValueError("max_concurrency must be >= 1")


def _resolve_output_paths(
    *,
    source_path: Path,
    output_dir: str | Path,
    write_markdown: bool,
) -> PdfOutputPaths:
    """Create the output directory and return all artifact paths."""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    return PdfOutputPaths(
        output_dir=output_path,
        sqlite_path=output_path / f"{source_path.stem}_llm_tables.sqlite",
        json_path=output_path / f"{source_path.stem}_llm_tables.json",
        markdown_path=output_path / f"{source_path.stem}_llm_tables.md" if write_markdown else None,
    )


def _chunk_ranges_for_pdf(
    pdf_path: Path,
    *,
    pages_per_chunk: int,
    max_chunks: int | None,
) -> list[tuple[int, int]]:
    """Return the page ranges that should be rendered for a PDF."""
    with pymupdf.open(str(pdf_path)) as document:
        ranges = _chunk_ranges(document.page_count, pages_per_chunk)
    if max_chunks is None:
        return ranges
    return ranges[: max(0, max_chunks)]


def _render_chunks(
    pdf_path: Path,
    *,
    ranges: list[tuple[int, int]],
    dpi: int,
) -> list[PdfChunk]:
    """Render all selected PDF page ranges."""
    return [
        _render_chunk(
            pdf_path,
            chunk_index=chunk_index,
            start_page=start_page,
            end_page=end_page,
            dpi=dpi,
        )
        for chunk_index, (start_page, end_page) in enumerate(ranges, start=1)
    ]


def _run_chunk_ocr_batch(
    chunks: list[PdfChunk],
    *,
    model: str,
    max_concurrency: int,
    config: RunnableConfig | None = None,
) -> tuple[dict[int, list[dict[str, Any]]], OcrUsage]:
    """Run chunk OCR concurrently and return normalized tables by chunk."""
    tables_by_chunk: dict[int, list[dict[str, Any]]] = {}
    usages: list[OcrUsage] = []
    with ThreadPoolExecutor(max_workers=min(max_concurrency, max(1, len(chunks)))) as executor:
        futures = {
            executor.submit(
                copy_context().run,
                _run_chunk_ocr,
                chunk,
                model=model,
                config=config,
            ): chunk
            for chunk in chunks
        }
        for future in as_completed(futures):
            chunk = futures[future]
            payload, usage = future.result()
            tables_by_chunk[chunk.index] = _normalize_chunk_tables(payload, chunk=chunk)
            usages.append(usage)
    return tables_by_chunk, _merge_usage(usages)


def _write_outputs(
    *,
    paths: PdfOutputPaths,
    pdf_path: Path,
    tables: list[dict[str, Any]],
) -> None:
    """Write SQLite, JSON, and optional Markdown artifacts."""
    _write_sqlite(paths.sqlite_path, tables)
    _write_json(paths.json_path, tables)
    if paths.markdown_path is not None:
        _write_markdown(paths.markdown_path, pdf_path=pdf_path, tables=tables)


@traceable(
    run_type="chain",
    process_inputs=_trace_pdf_inputs,
    process_outputs=_trace_pdf_outputs,
)
def extract_pdf_tables_to_csv(
    pdf_path: str | Path,
    *,
    output_dir: str | Path = "data/pdf_ocr",
    model: str | None = None,
    pages_per_chunk: int = DEFAULT_PAGES_PER_CHUNK,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    dpi: int = DEFAULT_DPI,
    max_chunks: int | None = None,
    fix_bridges: bool = True,
    fix_overall: bool = True,
    write_markdown: bool = True,
    config: RunnableConfig | None = None,
) -> PdfTableOcrResult:
    """Extract visually detected PDF tables with chunked LLM OCR, fixer cleanup, and CSV/Markdown output."""
    source_path = Path(pdf_path).expanduser().resolve()
    if not source_path.exists():
        raise FileNotFoundError(f"PDF not found: {source_path}")
    _validate_ocr_options(pages_per_chunk=pages_per_chunk, max_concurrency=max_concurrency)

    resolved_model = _resolve_ocr_model(model)
    paths = _resolve_output_paths(source_path=source_path, output_dir=output_dir, write_markdown=write_markdown)
    _remove_stale_csv_outputs(paths.output_dir, pdf_stem=source_path.stem)

    ranges = _chunk_ranges_for_pdf(source_path, pages_per_chunk=pages_per_chunk, max_chunks=max_chunks)
    chunks = _render_chunks(source_path, ranges=ranges, dpi=dpi)
    tables_by_chunk, usage = _run_chunk_ocr_batch(
        chunks,
        model=resolved_model,
        max_concurrency=max_concurrency,
        config=config,
    )

    if fix_bridges:
        tables, bridge_usage = _fix_table_bridges(
            source_path,
            chunks,
            tables_by_chunk,
            model=resolved_model,
            dpi=dpi,
            config=config,
        )
        usage = _merge_usage([usage, bridge_usage])
    else:
        tables = _ordered_tables_from_chunks(chunks, tables_by_chunk)

    if fix_overall:
        tables, overall_usage = _fix_overall_tables(
            tables,
            model=resolved_model,
            config=config,
        )
        usage = _merge_usage([usage, overall_usage])

    _write_outputs(paths=paths, pdf_path=source_path, tables=tables)

    return PdfTableOcrResult(
        pdf_path=source_path,
        output_dir=paths.output_dir,
        sqlite_path=paths.sqlite_path,
        json_path=paths.json_path,
        markdown_path=paths.markdown_path,
        table_count=len(tables),
        row_count=sum(len(table["rows"]) for table in tables),
        usage=usage,
    )
