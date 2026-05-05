"""LangGraph workflow for PDF text extraction, LangExtract, and JSON fixing."""

from __future__ import annotations

from functools import cache
import json
import os
from pathlib import Path
from typing import Any

import langextract as lx
from langextract.factory import ModelConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel, Field
import pymupdf

from src.config import DEFAULT_AGENT_MODEL, SKILLS_DIR, resolve_agent_model
from src.tools.fixer import fix_text
from src.tools.skills import load_skills
from src.tools.tabular.storage import fingerprint, load_tables_into_sqlite

DEFAULT_PDF_LANGEXTRACT_MODEL = DEFAULT_AGENT_MODEL
DEFAULT_OUTPUT_DIR = Path("data/langextract")
SKILLS_PATH = str(SKILLS_DIR)
DEFAULT_MAX_TEXT_CHARS = 12_000

DEFAULT_LANGEXTRACT_PROMPT = """Extract structured facts and table-like rows from the document text.
Use exact text from the source. Do not paraphrase extraction_text."""


class PdfLangExtractWorkflowInput(BaseModel):
    """Inputs for the experimental PDF LangExtract fixer workflow."""

    pdf_path: str = Field(description="PDF path to parse")
    output_dir: str = Field(default=str(DEFAULT_OUTPUT_DIR), description="Directory for draft and fixed JSON artifacts")
    prompt_description: str = Field(default=DEFAULT_LANGEXTRACT_PROMPT, description="LangExtract extraction instructions")
    examples: list[dict[str, Any]] = Field(default_factory=list, description="LangExtract examples encoded as plain dictionaries")
    table_headings: list[str] = Field(default_factory=list, description="Expected table headings for the fixer context")
    skill_names: list[str] = Field(default_factory=list, description="Workspace skill names that provide domain-specific PDF table rules")
    ocr_context_path: str | None = Field(default=None, description="Optional OCR table JSON artifact used as fixer evidence")
    use_ocr_context: bool = Field(default=True, description="Whether to auto-load an OCR table artifact for the fixer when available")
    fixer_instructions: str = Field(default="", description="Domain-specific fixer rules")
    langextract_model: str | None = Field(default=None, description="Model id for LangExtract's OpenAI-compatible provider")
    fixer_model: str | None = Field(default=None, description="Model id for the JSON fixer")
    max_text_chars: int = Field(default=DEFAULT_MAX_TEXT_CHARS, ge=1, description="Maximum extracted PDF text passed to LangExtract")
    run_fixer: bool = Field(default=True, description="Whether to run the fixer node after LangExtract")
    load_to_sqlite: bool = Field(default=True, description="Whether to load fixed DB-ready tables into the shared SQLite cache")


class PdfLangExtractWorkflowOutput(BaseModel):
    """Public output for the experimental PDF LangExtract fixer workflow."""

    pdf_path: str = ""
    result_json_path: str = ""
    database_path: str = ""
    langextract_model: str = ""
    fixer_model: str = ""
    extraction_count: int = 0
    draft_row_count: int = 0
    fixed_row_count: int = 0
    import_table_count: int = 0
    import_row_count: int = 0
    loaded_tables: list[dict[str, Any]] = Field(default_factory=list)


class PdfLangExtractWorkflowState(
    PdfLangExtractWorkflowInput,
    PdfLangExtractWorkflowOutput,
):
    """Graph state shared by PDF LangExtract and fixer nodes."""

    source_lines: list[str] = Field(default_factory=list)
    draft_payload: dict[str, Any] = Field(default_factory=dict)


def extract_pdf_text(pdf_path: str | Path) -> str:
    """Return plain text from every page of a PDF."""
    with pymupdf.open(str(pdf_path)) as document:
        return "\n".join(page.get_text("text") for page in document)


def langextract_model_config(model_id: str | None = None) -> ModelConfig:
    """Return an OpenAI-compatible LangExtract model config from repo env vars."""
    resolved_model = model_id or os.getenv("LANGEXTRACT_MODEL") or os.getenv("LLM_MODEL") or DEFAULT_PDF_LANGEXTRACT_MODEL
    api_key = os.getenv("LANGEXTRACT_API_KEY") or os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("Set LANGEXTRACT_API_KEY, LLM_API_KEY, or OPENAI_API_KEY before running this workflow.")

    provider_kwargs = {"api_key": api_key}
    base_url = os.getenv("LANGEXTRACT_BASE_URL") or os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL")
    if base_url:
        provider_kwargs["base_url"] = base_url

    return ModelConfig(model_id=resolved_model, provider="openai", provider_kwargs=provider_kwargs)


def _extraction_payload(extraction: Any) -> dict[str, Any]:
    interval = extraction.char_interval
    return {
        "class": extraction.extraction_class,
        "text": extraction.extraction_text,
        "attributes": extraction.attributes or {},
        "char_interval": None if interval is None else {"start": interval.start_pos, "end": interval.end_pos},
    }


def _example_from_payload(payload: dict[str, Any]) -> lx.data.ExampleData:
    """Convert a JSON-friendly example payload into LangExtract objects."""
    return lx.data.ExampleData(
        text=str(payload.get("text") or ""),
        extractions=[
            lx.data.Extraction(
                extraction_class=str(extraction.get("class") or extraction.get("extraction_class") or ""),
                extraction_text=str(extraction.get("text") or extraction.get("extraction_text") or ""),
                attributes=extraction.get("attributes") or {},
            )
            for extraction in payload.get("extractions", [])
            if isinstance(extraction, dict)
        ],
    )


def _row_from_extraction(extraction: dict[str, Any]) -> dict[str, Any]:
    attributes = extraction.get("attributes") or {}
    text_lines = str(extraction.get("text") or "").splitlines()
    label = attributes.get("label") or (text_lines[0] if text_lines else "")
    amount = attributes.get("amount") or (text_lines[-1] if len(text_lines) > 1 else "")
    return {
        "label": label,
        "amount": amount,
        "row_role": attributes.get("row_role") or "",
        "parent_label": attributes.get("parent_label") or "",
        "source_text": extraction.get("text") or "",
        "char_interval": extraction.get("char_interval"),
    }


def _fact_from_extraction(extraction: dict[str, Any]) -> dict[str, Any]:
    attributes = extraction.get("attributes") or {}
    return {
        "field": attributes.get("field") or extraction.get("class"),
        "value": extraction.get("text") or "",
        "char_interval": extraction.get("char_interval"),
    }


def _draft_payload(
    *,
    pdf_path: str,
    text: str,
    extractions: list[dict[str, Any]],
    table_headings: list[str],
) -> dict[str, Any]:
    tables: dict[str, dict[str, Any]] = {}
    facts: list[dict[str, Any]] = []
    for extraction in extractions:
        if extraction.get("class") == "billing_row":
            attributes = extraction.get("attributes") or {}
            table_name = str(attributes.get("table") or "Unassigned")
            table = tables.setdefault(table_name, {"title": table_name, "rows": []})
            table["rows"].append(_row_from_extraction(extraction))
        else:
            facts.append(_fact_from_extraction(extraction))

    return {
        "pdf_path": pdf_path,
        "facts": facts,
        "tables": list(tables.values()),
        "source_headings": [heading for heading in table_headings if heading in text],
    }


def _loaded_skills_context(skill_names: list[str]) -> str:
    """Load selected workspace skills as fixer context."""
    if not skill_names:
        return "none"

    loaded_blocks: list[str] = []
    diagnostics: list[str] = []
    for skill_name in skill_names:
        payload = load_skills.func(path=SKILLS_PATH, skills=skill_name)
        diagnostics.extend(str(item) for item in payload.get("diagnostics", []))
        loaded = payload.get("skills", [])
        if not loaded:
            diagnostics.append(f"Skill not found: {skill_name}")
            continue
        skill = loaded[0]
        content = ((skill.get("instructions") or {}).get("content") or "").strip()
        loaded_blocks.append(f"## {skill.get('name')}\n{content}")

    if diagnostics:
        loaded_blocks.append("## Skill diagnostics\n" + "\n".join(f"- {diagnostic}" for diagnostic in diagnostics))
    return "\n\n".join(loaded_blocks) if loaded_blocks else "none"


def _default_ocr_context_path(pdf_path: str | Path) -> Path:
    """Return the default OCR JSON artifact path for a PDF."""
    source_path = Path(pdf_path).expanduser().resolve()
    return Path.cwd() / "data" / "pdf_ocr" / f"{source_path.stem}_llm_tables.json"


def _load_ocr_context(state: PdfLangExtractWorkflowState) -> dict[str, Any]:
    """Load OCR table context for the fixer when configured and available."""
    if state.ocr_context_path:
        path = Path(state.ocr_context_path).expanduser().resolve()
    elif state.use_ocr_context:
        path = _default_ocr_context_path(state.pdf_path)
    else:
        return {}
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        "path": str(path),
        "payload": payload,
    }


def _ocr_context_text(ocr_context: dict[str, Any]) -> str:
    """Return a compact OCR table context block for the fixer."""
    if not ocr_context:
        return "none"

    payload = ocr_context.get("payload") or {}
    lines = [f"Source: {ocr_context.get('path')}"]
    for table in payload.get("tables", []):
        if not isinstance(table, dict):
            continue
        rows = table.get("rows", [])
        lines.append(f"\n[{table.get('title') or table.get('name')}]")
        for row in rows:
            if isinstance(row, list):
                lines.append(" | ".join(str(cell) for cell in row))
            elif isinstance(row, dict):
                lines.append(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(lines)


def _ocr_row_keys(ocr_context: dict[str, Any]) -> set[tuple[str, str]]:
    """Return label/amount pairs from OCR table rows."""
    payload = ocr_context.get("payload") or {}
    row_keys: set[tuple[str, str]] = set()
    for table in payload.get("tables", []):
        if not isinstance(table, dict):
            continue
        for row in table.get("rows", []):
            if isinstance(row, list) and len(row) >= 2:
                row_keys.add((str(row[0]), str(row[1])))
            elif isinstance(row, dict):
                label = row.get("label")
                amount = row.get("amount")
                if label is not None and amount is not None:
                    row_keys.add((str(label), str(amount)))
    return row_keys


def _filter_import_rows_by_ocr(
    import_payload: dict[str, Any],
    ocr_context: dict[str, Any],
) -> dict[str, Any]:
    """Drop import rows that do not exist as label/amount pairs in OCR context."""
    row_keys = _ocr_row_keys(ocr_context)
    if not row_keys:
        return import_payload

    filtered_tables: list[dict[str, Any]] = []
    for table in import_payload.get("tables", []):
        columns = list(table.get("columns", []))
        try:
            label_index = columns.index("label")
            amount_index = columns.index("amount")
        except ValueError:
            filtered_tables.append(table)
            continue

        rows = [
            row
            for row in table.get("rows", [])
            if isinstance(row, list) and len(row) > max(label_index, amount_index) and (str(row[label_index]), str(row[amount_index])) in row_keys
        ]
        if rows:
            filtered_tables.append({**table, "rows": rows})

    return {
        **import_payload,
        "tables": filtered_tables,
    }


def _row_count(payload: dict[str, Any]) -> int:
    return sum(len(table.get("rows", [])) for table in payload.get("tables", []) if isinstance(table, dict))


def langextract_node(state: PdfLangExtractWorkflowState | dict[str, Any]) -> dict[str, Any]:
    """Extract PDF text and run LangExtract as the first graph node."""
    workflow_state = PdfLangExtractWorkflowState.model_validate(state)
    pdf_path = Path(workflow_state.pdf_path).expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    text = extract_pdf_text(pdf_path)[: workflow_state.max_text_chars]
    config = langextract_model_config(workflow_state.langextract_model)
    result = lx.extract(
        text_or_documents=text,
        prompt_description=workflow_state.prompt_description,
        examples=[_example_from_payload(payload) for payload in workflow_state.examples],
        config=config,
        max_char_buffer=max(len(text), 1),
        batch_length=1,
        max_workers=1,
        temperature=0.0,
        extraction_passes=1,
        show_progress=False,
    )
    extractions = [_extraction_payload(extraction) for extraction in result.extractions]
    draft_payload = _draft_payload(
        pdf_path=str(pdf_path),
        text=text,
        extractions=extractions,
        table_headings=workflow_state.table_headings,
    )
    return {
        "pdf_path": str(pdf_path),
        "source_lines": [line for line in text.splitlines() if line.strip()],
        "langextract_model": str(config.model_id or ""),
        "draft_payload": draft_payload,
        "extraction_count": len(extractions),
        "draft_row_count": _row_count(draft_payload),
    }


def _fixer_context(state: PdfLangExtractWorkflowState) -> str:
    source_lines = "\n".join(f"{idx + 1:03d}: {line}" for idx, line in enumerate(state.source_lines))
    headings_block = ", ".join(state.table_headings) if state.table_headings else "none supplied"
    skills_block = _loaded_skills_context(state.skill_names)
    ocr_context = _load_ocr_context(state)
    ocr_block = _ocr_context_text(ocr_context)
    domain_rules = f"\nDomain-specific rules:\n{state.fixer_instructions}\n" if state.fixer_instructions else ""
    return f"""Source PDF text lines:
{source_lines}

OCR table context:
{ocr_block}

Expected table headings:
{headings_block}

Loaded skills:
{skills_block}

Rules:
- Treat loaded skills as authoritative for the final table shape.
- If a loaded skill defines final table names, rewrite the draft into those DB-ready tables.
- Use OCR table context as visual/table-boundary evidence when it conflicts with flattened LangExtract text.
- When OCR table context is available, treat its rows as the source of truth for importable table rows.
- Do not promote invoice header facts into importable tables unless they appear as rows in the OCR table context.
- Fix the JSON so tables and rows match the source PDF text lines.
- Preserve facts when correct.
- Keep provenance metadata out of final importable row values.
- Use row_role parent for top-level charge/account/service rows, child for indented component rows, and total for subtotal/total rows.
- Set parent_label on child rows to the nearest preceding parent row in the same table.
- Do not invent rows that are absent from the source text.
{domain_rules}
"""


PDF_TABLE_FIXER_SYSTEM_PROMPT = """Edit the JSON file directly to repair PDF table structure.

The JSON is a draft created by LangExtract. It may have correct labels and amounts but incorrect table names, missing zero-amount rows, or missing parent/child roles.

Hard constraints:
- Return valid JSON.
- Keep the top-level shape parseable as a PDF extraction artifact.
- If loaded skill instructions define final tables, the output tables must use those table names and columns.
- Each final/importable table should have name, columns, and rows whenever possible.
- Importable table rows must come from table-like rows in the OCR context or source table streams, not from invoice header facts.
- Do not put provenance fields such as source_text, char_interval, raw extraction metadata, page hints, or confidence into importable row cells.
- Use only source text supplied in context as evidence.
- Prefer adding/fixing concrete missing rows over style cleanup.
"""


NON_IMPORT_ROW_KEYS = {
    "char_interval",
    "confidence",
    "extraction_index",
    "metadata",
    "page",
    "page_number",
    "provenance",
    "raw",
    "source",
    "source_text",
    "span",
}


def _cell_text(value: Any) -> str:
    """Return one SQLite-ready text cell."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")) if isinstance(value, dict | list) else str(value)


def _columns_from_dict_rows(rows: list[dict[str, Any]]) -> list[str]:
    """Return stable import columns from row dictionaries."""
    columns: list[str] = []
    for row in rows:
        for key in row:
            if key in NON_IMPORT_ROW_KEYS or key in columns:
                continue
            columns.append(key)
    return columns


def _normalize_table_for_import(
    table: dict[str, Any],
    table_index: int,
) -> dict[str, Any]:
    """Return one fixed table as DB-ready rows."""
    table_name = str(table.get("name") or table.get("title") or f"table_{table_index}")
    rows = table.get("rows", [])
    if not isinstance(rows, list):
        rows = []

    if rows and all(isinstance(row, list) for row in rows):
        columns = [str(column) for column in table.get("columns", [])]
        if not columns:
            width = max((len(row) for row in rows if isinstance(row, list)), default=0)
            columns = [f"column_{idx}" for idx in range(1, width + 1)]
        import_rows = [[_cell_text(cell) for cell in row] for row in rows if isinstance(row, list)]
    else:
        dict_rows = [row for row in rows if isinstance(row, dict)]
        columns = [str(column) for column in table.get("columns", []) if str(column) not in NON_IMPORT_ROW_KEYS]
        if not columns:
            columns = _columns_from_dict_rows(dict_rows)
        import_rows = []
        for row in dict_rows:
            import_rows.append([_cell_text(row.get(column)) for column in columns])

    return {
        "name": table_name,
        "columns": columns,
        "rows": import_rows,
    }


def _normalize_payload_for_import(
    payload: dict[str, Any],
    *,
    source_path: str,
) -> dict[str, Any]:
    """Return DB-ready tables from a fixed extraction payload."""
    import_tables: list[dict[str, Any]] = []
    for table_index, table in enumerate(payload.get("tables", []), start=1):
        if not isinstance(table, dict):
            continue
        import_table = _normalize_table_for_import(table, table_index)
        if import_table["columns"]:
            import_tables.append(import_table)

    return {
        "path": source_path,
        "format": "pdf_langextract",
        "tables": import_tables,
    }


def _import_fingerprint(import_payload: dict[str, Any]) -> str:
    """Build a deterministic fingerprint for normalized PDF import tables."""
    rows: list[list[str]] = []
    for table in import_payload.get("tables", []):
        rows.append([str(table.get("name") or "")])
        rows.append([str(column) for column in table.get("columns", [])])
        rows.extend([str(cell) for cell in row] for row in table.get("rows", []))
    return fingerprint(rows, max_sample_rows=max(len(rows), 1), header_candidates=[])


def _fixed_payload_from_state(state: PdfLangExtractWorkflowState) -> tuple[dict[str, Any], str]:
    """Return the fixed extraction payload and resolved fixer model."""
    fixer_model = resolve_agent_model(state.fixer_model)
    if not state.run_fixer:
        return state.draft_payload, fixer_model

    fixed_text = fix_text(
        text=json.dumps(state.draft_payload, indent=2),
        fixer_model=state.fixer_model,
        fixer_context=_fixer_context(state),
        fixer_system_prompt=PDF_TABLE_FIXER_SYSTEM_PROMPT,
        max_iterations=2,
        sandbox_file_name="langextract_tables.json",
    )
    return json.loads(fixed_text), fixer_model


def fixer_node(state: PdfLangExtractWorkflowState | dict[str, Any]) -> dict[str, Any]:
    """Run the existing text fixer after LangExtract to repair JSON structure."""
    workflow_state = PdfLangExtractWorkflowState.model_validate(state)
    fixed_payload, fixer_model = _fixed_payload_from_state(workflow_state)
    import_payload = _normalize_payload_for_import(
        fixed_payload,
        source_path=workflow_state.pdf_path,
    )
    import_payload = _filter_import_rows_by_ocr(import_payload, _load_ocr_context(workflow_state))

    pdf_path = Path(workflow_state.pdf_path).expanduser().resolve()
    result_path = Path(workflow_state.output_dir).expanduser().resolve() / f"{pdf_path.stem}_langextract_result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(import_payload, indent=2), encoding="utf-8")

    database_path = ""
    loaded_tables: list[dict[str, Any]] = []
    if workflow_state.load_to_sqlite and import_payload["tables"]:
        loaded = load_tables_into_sqlite(
            import_payload,
            fingerprint=_import_fingerprint(import_payload),
        )
        database_path = loaded["database_path"]
        loaded_tables = loaded["tables"]

    return {
        "result_json_path": str(result_path),
        "database_path": database_path,
        "fixer_model": fixer_model,
        "fixed_row_count": _row_count(fixed_payload),
        "import_table_count": len(import_payload["tables"]),
        "import_row_count": _row_count(import_payload),
        "loaded_tables": loaded_tables,
    }


@cache
def create_pdf_langextract_fixer_graph() -> CompiledStateGraph:
    """Build the two-node PDF LangExtract -> fixer workflow."""
    builder = StateGraph(
        PdfLangExtractWorkflowState,
        input_schema=PdfLangExtractWorkflowInput,
        output_schema=PdfLangExtractWorkflowOutput,
    )
    builder.add_node("langextract", langextract_node)
    builder.add_node("fixer", fixer_node)
    builder.add_edge(START, "langextract")
    builder.add_edge("langextract", "fixer")
    builder.add_edge("fixer", END)
    return builder.compile()


def extract_pdf_tables_with_langextract_fixer(
    pdf_path: str | Path,
    *,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    prompt_description: str = DEFAULT_LANGEXTRACT_PROMPT,
    examples: list[dict[str, Any]] | None = None,
    table_headings: list[str] | None = None,
    skill_names: list[str] | None = None,
    ocr_context_path: str | Path | None = None,
    use_ocr_context: bool = True,
    fixer_instructions: str = "",
    langextract_model: str | None = None,
    fixer_model: str | None = None,
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
    run_fixer: bool = True,
    load_to_sqlite: bool = True,
) -> dict[str, Any]:
    """Run the PDF LangExtract fixer workflow and return the graph output."""
    graph_input = PdfLangExtractWorkflowInput(
        pdf_path=str(pdf_path),
        output_dir=str(output_dir),
        prompt_description=prompt_description,
        examples=examples or [],
        table_headings=table_headings or [],
        skill_names=skill_names or [],
        ocr_context_path=str(ocr_context_path) if ocr_context_path else None,
        use_ocr_context=use_ocr_context,
        fixer_instructions=fixer_instructions,
        langextract_model=langextract_model,
        fixer_model=fixer_model,
        max_text_chars=max_text_chars,
        run_fixer=run_fixer,
        load_to_sqlite=load_to_sqlite,
    )
    graph = create_pdf_langextract_fixer_graph()
    output = graph.invoke(graph_input.model_dump())
    return PdfLangExtractWorkflowOutput.model_validate(output).model_dump()
