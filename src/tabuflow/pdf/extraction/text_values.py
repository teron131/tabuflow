"""Text-line value extraction strategies for PDFs."""

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .pages import document_lines


def compiled_contexts(config: dict[str, Any], key: str) -> list[tuple[str, re.Pattern[str]]]:
    """Return configured line-context patterns."""
    return [(str(item["name"]), re.compile(str(item["pattern"]))) for item in config.get(key, [])]


def context_match_value(match: re.Match[str]) -> str:
    """Return the carried value for a context regex match."""
    if "value" in match.groupdict():
        return str(match.group("value"))
    if match.groups():
        return str(next((group for group in match.groups() if group is not None), match.group(0)))
    return str(match.group(0))


def update_line_context(
    text: str,
    context: dict[str, str],
    contexts: list[tuple[str, re.Pattern[str]]],
    clear_contexts: list[tuple[str, re.Pattern[str]]],
) -> None:
    """Update carried context values from one cleaned text line."""
    for name, pattern in clear_contexts:
        if pattern.match(text):
            context[name] = ""
    for name, pattern in contexts:
        if match := pattern.match(text):
            context[name] = context_match_value(match)


def line_value_rows(
    pdf_path: Path,
    config: dict[str, Any],
) -> list[dict[str, str]]:
    """Extract adjacent label/value text-line pairs."""
    value_pattern = re.compile(str(config["value_pattern"]))
    label_column = str(config.get("label_column", "label"))
    value_column = str(config.get("value_column", "value"))
    contexts = compiled_contexts(config, "contexts")
    clear_contexts = compiled_contexts(config, "clear_contexts")
    context = {name: "" for name, _pattern in contexts}
    records = document_lines(pdf_path, config)
    rows: list[dict[str, str]] = []
    line_index = 0
    while line_index < len(records) - 1:
        label_record = records[line_index]
        value_record = records[line_index + 1]
        label = str(label_record["text"])
        value = str(value_record["text"])
        update_line_context(label, context, contexts, clear_contexts)
        if value_pattern.match(value):
            row = dict(context)
            row.update(
                {
                    label_column: label,
                    value_column: value,
                }
            )
            if config.get("include_page"):
                row["page"] = str(label_record["page"])
            rows.append(row)
            line_index += 2
            continue
        line_index += 1
    return rows


def field_value_rows(
    pdf_path: Path,
    config: dict[str, Any],
) -> list[dict[str, str]]:
    """Extract configured field names and following value lines."""
    field_column = str(config.get("field_column", "field"))
    value_column = str(config.get("value_column", "value"))
    field_labels = dict(config["fields"])
    collect_until_next_field = bool(config.get("collect_until_next_field"))
    field_names = set(field_labels)
    contexts = compiled_contexts(config, "contexts")
    clear_contexts = compiled_contexts(config, "clear_contexts")
    context = {name: "" for name, _pattern in contexts}
    records = document_lines(pdf_path, config)
    rows: list[dict[str, str]] = []
    for index, record in enumerate(records[:-1]):
        line = str(record["text"])
        update_line_context(line, context, contexts, clear_contexts)
        if line not in field_labels:
            continue
        values = [str(records[index + 1]["text"])]
        if collect_until_next_field:
            for next_record in records[index + 2 :]:
                next_line = str(next_record["text"])
                if next_line in field_names:
                    break
                values.append(next_line)
        row = dict(context)
        row.update(
            {
                field_column: str(field_labels[line]),
                value_column: " ".join(values),
            }
        )
        if config.get("include_page"):
            row["page"] = str(record["page"])
        rows.append(row)
    return rows
