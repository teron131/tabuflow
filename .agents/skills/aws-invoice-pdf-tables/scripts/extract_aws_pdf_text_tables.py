#!/usr/bin/env python3
"""Extract AWS invoice-style label/amount tables from text PDFs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
from typing import Any

import fitz


AMOUNT_RE = re.compile(r"^(?P<sign>-)?USD\s+(?P<number>[0-9][0-9,]*\.[0-9]{2})$")
INLINE_AMOUNT_RE = re.compile(r"^(?P<label>.+?)\s+(?P<amount>-?USD\s+[0-9][0-9,]*\.[0-9]{2})$")
CHILD_LABELS = {
    "charges",
    "credits",
    "tax",
    "vat **",
    "gst",
    "ct",
    "estimated us sales tax to be collected",
    "discount (bundled discount)",
    "discount (enterprise discount program)",
    "savings plan (charges covered by savings plans)",
    "savings plans for aws compute usage",
}
STOP_PREFIXES = (
    "* may include",
    "amazon web services, inc. is registered",
    "aws, inc. is a",
    "** this is not",
    "**** please reference",
    "† usage and recurring",
    "all charges and prices",
    "all aws services are sold",
    "electronic funds transfer",
    "for line item details",
)


def normalize_amount(value: str) -> str:
    value = value.strip()
    match = AMOUNT_RE.match(value)
    if not match:
        return value
    sign = "-" if match.group("sign") else ""
    return f"{sign}{match.group('number').replace(',', '')}"


def normalize_row_amount(label: str, amount: str) -> str:
    normalized = normalize_amount(amount)
    if label.lower().startswith("less ") and not normalized.startswith("-"):
        return f"-{normalized}"
    return normalized


def clean_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def should_stop_table(line: str) -> bool:
    lowered = line.lower()
    return lowered.startswith(STOP_PREFIXES)


def is_amount(line: str) -> bool:
    return bool(AMOUNT_RE.match(line.strip()))


def split_inline_amount(line: str) -> tuple[str, str] | None:
    match = INLINE_AMOUNT_RE.match(line.strip())
    if not match:
        return None
    return match.group("label").strip(), match.group("amount").strip()


def row_role(label: str) -> str:
    lowered = label.lower()
    if lowered.startswith("total"):
        return "total"
    if lowered in CHILD_LABELS or lowered.startswith(("discount ", "savings plan")):
        return "child"
    return "parent"


def is_invoice_header_amount(label: str) -> bool:
    lowered = label.lower()
    return lowered.startswith(("total amount due", "total amount"))


def invoice_metadata(lines: list[str]) -> dict[str, str]:
    metadata = {}
    for idx, line in enumerate(lines):
        if line == "Account number:" and idx + 1 < len(lines):
            metadata["account_number"] = lines[idx + 1]
        if line == "Invoice Number:" and idx + 1 < len(lines):
            metadata["invoice_number"] = lines[idx + 1]
        if line == "Invoice Date:" and idx + 1 < len(lines):
            metadata["invoice_date"] = lines[idx + 1]
    return metadata


def extract_rows_from_lines(lines: list[str], *, page_number: int, metadata: dict[str, str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    section = ""
    current_parent = ""
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        lowered = line.lower()
        if should_stop_table(line):
            break
        if lowered in {"summary", "detail", "detail for consolidated bill", "activity by account"}:
            section = line
            current_parent = ""
            idx += 1
            continue

        label = ""
        amount = ""
        inline = split_inline_amount(line)
        if inline:
            label, amount = inline
        elif idx + 1 < len(lines) and is_amount(lines[idx + 1]):
            label, amount = line, lines[idx + 1]
            idx += 1

        if label and amount:
            if is_invoice_header_amount(label):
                idx += 1
                continue
            role = row_role(label)
            parent_label = current_parent if role == "child" else ""
            if role == "parent":
                current_parent = label
            rows.append(
                {
                    "page": str(page_number),
                    "section": section or "Detail",
                    "label": label,
                    "amount": normalize_row_amount(label, amount),
                    "row_role": role,
                    "parent_label": parent_label,
                    "account_number": metadata.get("account_number", ""),
                    "invoice_number": metadata.get("invoice_number", ""),
                    "invoice_date": metadata.get("invoice_date", ""),
                }
            )
        idx += 1
    return rows


def extract_pdf(path: Path) -> dict[str, Any]:
    document = fitz.open(path)
    all_rows: list[dict[str, str]] = []
    page_summaries = []
    total_text_line_count = 0
    first_page_lines = clean_lines(document[0].get_text("text")) if document.page_count else []
    metadata = invoice_metadata(first_page_lines)

    for page_idx, page in enumerate(document, start=1):
        text = page.get_text("text")
        lines = clean_lines(text)
        total_text_line_count += len(lines)
        rows = extract_rows_from_lines(lines, page_number=page_idx, metadata=metadata)
        all_rows.extend(rows)
        page_summaries.append(
            {
                "page": page_idx,
                "text_char_count": len(text),
                "text_line_count": len(lines),
                "extracted_amount_row_count": len(rows),
                "needs_ocr": not bool(text.strip()),
            }
        )

    return {
        "path": str(path),
        "page_count": document.page_count,
        "metadata": metadata,
        "extraction_route": "direct_pdf_text_label_amount_pairs",
        "text_line_count": total_text_line_count,
        "extracted_amount_row_count": len(all_rows),
        "columns": [
            "page",
            "section",
            "label",
            "amount",
            "row_role",
            "parent_label",
            "account_number",
            "invoice_number",
            "invoice_date",
        ],
        "rows": all_rows,
        "page_summaries": page_summaries,
    }


def safe_stem(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", path.stem).strip("_") or "pdf"


def write_outputs(result: dict[str, Any], output_dir: Path) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = safe_stem(Path(result["path"]))
    csv_path = output_dir / f"{stem}_tables.csv"
    json_path = output_dir / f"{stem}_tables.json"

    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=result["columns"])
        writer.writeheader()
        writer.writerows(result["rows"])
    json_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return {"csv_path": str(csv_path), "json_path": str(json_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", nargs="+", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("data/aws_pdf_tables"))
    args = parser.parse_args()

    summary = []
    for pdf_path in args.pdf:
        result = extract_pdf(pdf_path)
        paths = write_outputs(result, args.output_dir)
        summary.append(
            {
                "path": str(pdf_path),
                "page_count": result["page_count"],
                "extraction_route": result["extraction_route"],
                "text_line_count": result["text_line_count"],
                "extracted_amount_row_count": result["extracted_amount_row_count"],
                "pages_needing_ocr": [page["page"] for page in result["page_summaries"] if page["needs_ocr"]],
                **paths,
            }
        )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
