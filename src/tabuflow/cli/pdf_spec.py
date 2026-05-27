"""PDF extraction CLI option parsing and reusable rules merging."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from ..pdf.common import PDF_TABLE_SCALAR_TUNING_OPTIONS

PDF_TABLE_STRATEGIES = ["lines", "lines-strict", "text"]
PDF_VALUE_PRESETS = {
    "money": r"^-?[A-Z]{3}\s+[0-9][0-9,]*\.[0-9]{2}$",
    "number": r"^-?[0-9][0-9,]*(?:\.[0-9]+)?$",
}

PDF_TABLE_PRESET_MODES = {
    "detected": "pymupdf_tables",
    "coordinate": "coordinate_table",
    "field-value": "field_value",
    "line-value": "line_value",
}


def parse_comma_list(value: str | None) -> list[str]:
    """Return a comma-separated CLI value as a clean list."""
    if not value:
        return []
    return [part.strip() for part in value.split(",") if part.strip()]


def parse_field_mappings(values: list[str] | None) -> dict[str, str]:
    """Return repeated FIELD=OUTPUT arguments as a mapping."""
    fields: dict[str, str] = {}
    for value in values or []:
        if "=" not in value:
            raise ValueError(f"Field mappings must use FIELD=OUTPUT: {value}")
        field, output = value.split("=", 1)
        if not field.strip() or not output.strip():
            raise ValueError(f"Field mappings must use FIELD=OUTPUT: {value}")
        fields[field.strip()] = output.strip()
    return fields


def parse_coordinate_column(value: str) -> dict[str, str | float]:
    """Return NAME:X_MIN:X_MAX as a coordinate column config."""
    parts = value.split(":")
    if len(parts) != 3 or not parts[0].strip():
        raise ValueError(f"Coordinate columns must use NAME:X_MIN:X_MAX: {value}")
    return {
        "name": parts[0].strip(),
        "x_min": float(parts[1]),
        "x_max": float(parts[2]),
    }


def parse_clip_rect(value: str) -> list[float]:
    """Return X0,Y0,X1,Y1 as a clip rectangle value."""
    parts = [part.strip() for part in value.split(",") if part.strip()]
    if len(parts) != 4:
        raise ValueError(f"Clip rectangles must use X0,Y0,X1,Y1: {value}")
    return [float(part) for part in parts]


def parse_named_pattern(
    value: str,
    *,
    label: str,
) -> dict[str, str]:
    """Return FIELD=REGEX as a named line-pattern config."""
    if "=" not in value:
        raise ValueError(f"{label} must use FIELD=REGEX: {value}")
    name, pattern = value.split("=", 1)
    if not name.strip() or not pattern.strip():
        raise ValueError(f"{label} must use FIELD=REGEX: {value}")
    return {"name": name.strip(), "pattern": pattern.strip()}


def read_pdf_rules(path: str) -> dict[str, Any]:
    """Read a YAML PDF extraction rules file."""
    rules_path = Path(path).expanduser()
    payload = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"PDF rules must be a mapping: {rules_path}")
    return payload


def merge_pdf_rules(
    table: dict[str, Any],
    rules: dict[str, Any],
) -> None:
    """Merge reusable PDF extraction rules into a table config."""
    list_fields = (
        "skip_lines",
        "skip_prefixes",
        "stop_prefixes",
        "contexts",
        "clear_contexts",
        "columns",
        "required_columns",
        "output_columns",
    )
    scalar_fields = (
        "section",
        "value_pattern",
        "value_preset",
        "split_by",
        "drop_empty_split",
        "include_page",
        "label_column",
        "value_column",
        "field_column",
        "fields",
        "collect_until_next_field",
        "min_rows",
        "min_filled_cells",
        "require_header",
        "merge_tables",
        "strategy",
        "vertical_strategy",
        "horizontal_strategy",
        "clip",
        *PDF_TABLE_SCALAR_TUNING_OPTIONS,
        "y_min",
        "y_max",
        "y_tolerance",
        "anchor_y_slop",
        "continuation_column",
        "pages",
        "page_start",
        "page_end",
    )
    for key in list_fields:
        if values := rules.get(key):
            if not isinstance(values, list):
                raise ValueError(f"PDF rules field must be a list: {key}")
            table.setdefault(key, [])
            table[key].extend(values)
    for key in scalar_fields:
        if key in rules and key not in table:
            table[key] = rules[key]


def add_shared_pdf_table_options(
    args: Any,
    table: dict[str, Any],
) -> None:
    """Add CLI options shared by all PDF table presets."""
    if args.pages:
        table["pages"] = [int(page) for page in parse_comma_list(args.pages)]
    if args.page_start is not None:
        table["page_start"] = args.page_start
    if args.page_end is not None:
        table["page_end"] = args.page_end
    if args.include_page:
        table["include_page"] = True
    for key in ("skip_lines", "skip_prefixes", "stop_prefixes"):
        values = getattr(args, key)
        if values:
            table[key] = values
    if args.section:
        table.setdefault("contexts", [])
        table["contexts"].append({"name": "section", "pattern": args.section})
    elif section := table.pop("section", None):
        table.setdefault("contexts", [])
        table["contexts"].append({"name": "section", "pattern": str(section)})
    if args.context:
        table.setdefault("contexts", [])
        table["contexts"].extend(parse_named_pattern(value, label="--context") for value in args.context)
    if args.clear_context:
        table["clear_contexts"] = [parse_named_pattern(value, label="--clear-context") for value in args.clear_context]
    if args.split_by or args.split_sections:
        table["split_by"] = args.split_by or "section"
    if args.drop_empty_split:
        table["drop_empty_split"] = True
    if output_columns := parse_comma_list(args.output_columns):
        table["output_columns"] = output_columns


def add_detected_table_options(
    args: Any,
    table: dict[str, Any],
) -> None:
    """Add PyMuPDF detected-table extraction options."""
    table["min_rows"] = int(table.get("min_rows", args.min_rows))
    table["merge_tables"] = args.merge_tables or str(table.get("merge_tables", "auto"))
    if args.min_filled_cells is not None:
        table["min_filled_cells"] = args.min_filled_cells
    if args.require_header or table.get("require_header"):
        table["require_header"] = True
    strategy = args.strategy or table.get("strategy")
    if strategy:
        table["vertical_strategy"] = str(strategy).replace("-", "_")
        table["horizontal_strategy"] = str(strategy).replace("-", "_")
    if args.vertical_strategy:
        table["vertical_strategy"] = args.vertical_strategy.replace("-", "_")
    if args.horizontal_strategy:
        table["horizontal_strategy"] = args.horizontal_strategy.replace("-", "_")
    if args.clip:
        table["clip"] = parse_clip_rect(args.clip)
    for key in PDF_TABLE_SCALAR_TUNING_OPTIONS:
        value = getattr(args, key)
        if value is not None:
            table[key] = value


def add_line_value_table_options(
    args: Any,
    table: dict[str, Any],
) -> None:
    """Add label/value text extraction options."""
    value_preset = args.value_preset or table.get("value_preset")
    value_pattern = args.value_pattern or table.get("value_pattern") or PDF_VALUE_PRESETS.get(str(value_preset))
    if not value_pattern:
        raise ValueError("tables line-value preset requires --value-pattern or --value-preset.")
    table.update(
        {
            "value_pattern": value_pattern,
            "label_column": args.label_column or str(table.get("label_column", "label")),
            "value_column": args.value_column or str(table.get("value_column", "value")),
        }
    )
    if value_preset:
        table["value_preset"] = value_preset


def add_field_value_table_options(
    args: Any,
    table: dict[str, Any],
) -> None:
    """Add configured field/value text extraction options."""
    fields = parse_field_mappings(args.fields) if args.fields else dict(table.get("fields", {}))
    if not fields:
        raise ValueError("tables field-value preset requires at least one --field FIELD=OUTPUT.")
    table.update(
        {
            "fields": fields,
            "field_column": args.field_column or str(table.get("field_column", "field")),
            "value_column": args.value_column or str(table.get("value_column", "value")),
            "collect_until_next_field": args.collect_until_next_field or bool(table.get("collect_until_next_field")),
        }
    )


def add_coordinate_table_options(
    args: Any,
    table: dict[str, Any],
) -> None:
    """Add fixed-coordinate table extraction options."""
    if args.columns:
        table["columns"] = [parse_coordinate_column(value) for value in args.columns]
    if not table.get("columns"):
        raise ValueError("tables coordinate preset requires at least one --column NAME:X_MIN:X_MAX.")
    table.update(
        {
            "y_min": table.get("y_min", args.y_min),
            "y_max": table.get("y_max", args.y_max),
            "y_tolerance": table.get("y_tolerance", args.y_tolerance),
        }
    )
    if args.anchor_y_slop is not None:
        table["anchor_y_slop"] = args.anchor_y_slop
    if args.continuation_column:
        table["continuation_column"] = args.continuation_column
    if required_columns := parse_comma_list(args.required_columns):
        table["required_columns"] = required_columns


def pdf_extract_spec_from_args(args: Any) -> dict[str, Any]:
    """Build the internal PDF table extraction spec from preset CLI arguments."""
    rules = read_pdf_rules(args.rules) if args.rules else {}
    target = str(args.target or rules.get("target", "tables"))
    if target != "tables":
        raise ValueError(f"Unsupported PDF extraction target: {target}")
    preset = str(args.preset or rules.get("preset", ""))
    if preset not in PDF_TABLE_PRESET_MODES:
        raise ValueError("PDF extraction requires a preset argument or a rules file with preset.")
    internal_mode = PDF_TABLE_PRESET_MODES[preset]
    table: dict[str, Any] = {
        "name": args.name or str(rules.get("name") or ("detected_tables" if preset == "detected" else "table")),
        "preset": preset,
        "mode": internal_mode,
    }
    if rules:
        merge_pdf_rules(table, rules)
    add_shared_pdf_table_options(args, table)

    if preset == "detected":
        add_detected_table_options(args, table)
    elif preset == "line-value":
        add_line_value_table_options(args, table)
    elif preset == "field-value":
        add_field_value_table_options(args, table)
    elif preset == "coordinate":
        add_coordinate_table_options(args, table)

    return {"tables": [table]}
