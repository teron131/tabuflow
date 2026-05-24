"""Minimal command line wrappers around standalone Tabuflow tools."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

import yaml

from .artifacts import (
    DEFAULT_CLI_ARTIFACT_LIST_LIMIT,
    SQL_ARTIFACT_LIST_DETAILS,
    artifacts_from_source,
    describe_sql_artifact,
    list_sql_artifacts,
    query_artifacts,
    save_artifact_view,
    suggest_sql_artifacts,
)
from .email import inspect_email_file
from .pdf import (
    DEFAULT_DPI,
    DEFAULT_INSPECT_PAGE_LIMIT,
    DEFAULT_INSPECT_TEXT_CHARS,
    DEFAULT_MAX_PREPARE_PAGES,
    extract_pdf_file,
    inspect_pdf_file,
    prepare_pdf_file,
)
from .tabular import (
    MAX_METADATA_ROWS,
    MAX_SAMPLE_ROWS,
    extract_tabular_file,
    inspect_tabular_file,
    profile_tabular_file,
    profile_tabular_workbook_sheets,
)


def print_json(payload: Any) -> None:
    """Print one JSON payload for script-friendly consumption."""
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def read_sql_argument(
    sql: str,
    *,
    root_dir: Path | None = None,
) -> str:
    """Return inline SQL or the text from an @file argument."""
    sql_path = resolve_sql_argument_path(sql, root_dir=root_dir)
    if sql_path is None:
        return sql
    return sql_path.read_text(encoding="utf-8")


def resolve_sql_argument_path(
    sql: str,
    *,
    root_dir: Path | None = None,
) -> Path | None:
    """Return the resolved @file path for a SQL argument when present."""
    if not sql.startswith("@"):
        return None
    sql_path = Path(sql[1:]).expanduser()
    if not sql_path.is_absolute() and root_dir is not None:
        sql_path = root_dir / sql_path
    return sql_path.resolve()


def resolve_cli_root(args: argparse.Namespace) -> Path | None:
    """Resolve the optional CLI workspace root."""
    root_dir = getattr(args, "root_dir", None)
    if root_dir is None:
        return None
    return Path(root_dir).expanduser().resolve()


def resolve_cli_path(
    path: str,
    args: argparse.Namespace,
) -> Path:
    """Resolve a source path against the optional CLI workspace root."""
    source_path = Path(path).expanduser()
    if source_path.is_absolute():
        return source_path.resolve()
    root_dir = resolve_cli_root(args)
    if root_dir is None:
        return source_path
    return (root_dir / source_path).resolve()


def resolve_cli_database_path(args: argparse.Namespace) -> Path | None:
    """Resolve the optional artifact database path against the CLI workspace root."""
    database_path = getattr(args, "database_path", None)
    if database_path is None:
        return None
    path = Path(database_path).expanduser()
    if path.is_absolute():
        return path.resolve()
    root_dir = resolve_cli_root(args)
    if root_dir is None:
        return path
    return (root_dir / path).resolve()


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


def parse_named_pattern(value: str, *, label: str) -> dict[str, str]:
    """Return FIELD=REGEX as a named line-pattern config."""
    if "=" not in value:
        raise ValueError(f"{label} must use FIELD=REGEX: {value}")
    name, pattern = value.split("=", 1)
    if not name.strip() or not pattern.strip():
        raise ValueError(f"{label} must use FIELD=REGEX: {value}")
    return {"name": name.strip(), "pattern": pattern.strip()}


def read_pdf_rules(path: str, *, root_dir: Path | None = None) -> dict[str, Any]:
    """Read a YAML PDF extraction rules file."""
    rules_path = Path(path).expanduser()
    if not rules_path.is_absolute() and root_dir is not None:
        rules_path = root_dir / rules_path
    payload = yaml.safe_load(rules_path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"PDF rules must be a mapping: {rules_path}")
    return payload


def merge_pdf_rules(table: dict[str, Any], rules: dict[str, Any]) -> None:
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


def pdf_extract_spec_from_args(args: argparse.Namespace) -> dict[str, Any]:
    """Build the internal PDF table extraction spec from preset CLI arguments."""
    rules = read_pdf_rules(args.rules, root_dir=resolve_cli_root(args)) if args.rules else {}
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
        "number": args.number,
    }
    if args.pages:
        table["pages"] = [int(page) for page in parse_comma_list(args.pages)]
    if args.page_start is not None:
        table["page_start"] = args.page_start
    if args.page_end is not None:
        table["page_end"] = args.page_end
    if args.include_page:
        table["include_page"] = True
    if rules:
        merge_pdf_rules(table, rules)
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

    if preset == "detected":
        table["min_rows"] = int(table.get("min_rows", args.min_rows))
        table["merge_tables"] = args.merge_tables or str(table.get("merge_tables", "auto"))
        if args.min_filled_cells is not None:
            table["min_filled_cells"] = args.min_filled_cells
        if args.require_header or table.get("require_header"):
            table["require_header"] = True
        if args.strategy:
            table["vertical_strategy"] = args.strategy.replace("-", "_")
            table["horizontal_strategy"] = args.strategy.replace("-", "_")
        if args.vertical_strategy:
            table["vertical_strategy"] = args.vertical_strategy.replace("-", "_")
        if args.horizontal_strategy:
            table["horizontal_strategy"] = args.horizontal_strategy.replace("-", "_")
        if args.clip:
            table["clip"] = parse_clip_rect(args.clip)
    elif preset == "line-value":
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
    elif preset == "field-value":
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
    elif preset == "coordinate":
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
    if output_columns := parse_comma_list(args.output_columns):
        table["output_columns"] = output_columns

    return {"tables": [table]}


def add_root_argument(parser: argparse.ArgumentParser) -> None:
    """Add the shared workspace-root argument."""
    parser.add_argument("--root-dir", default=None, help="Workspace root for relative source paths and the default artifact database.")


def add_tabular_commands(subparsers: Any) -> None:
    """Add CSV/XLS/XLSX inspection and extraction commands."""
    tabular = subparsers.add_parser("tabular", help="Inspect or extract CSV/XLS/XLSX files.")
    add_root_argument(tabular)
    tabular_subparsers = tabular.add_subparsers(dest="tabular_command", required=True)

    inspect = tabular_subparsers.add_parser("inspect", help="Inspect a bounded raw grid window.")
    inspect.add_argument("path")
    inspect.add_argument("--start-row", type=int, default=1)
    inspect.add_argument("--limit", type=int, default=20)
    inspect.add_argument("--start-col", type=int, default=1)
    inspect.add_argument("--end-col", type=int, default=None)
    inspect.add_argument("--sheet", default=None)
    inspect.set_defaults(
        handler=lambda args: inspect_tabular_file(
            resolve_cli_path(args.path, args),
            start_row=args.start_row,
            limit=args.limit,
            start_col=args.start_col,
            end_col=args.end_col,
            sheet=args.sheet,
        )
    )

    profile = tabular_subparsers.add_parser("profile", help="Profile file structure without extraction.")
    profile.add_argument("path")
    profile.add_argument("--max-sample-rows", type=int, default=MAX_SAMPLE_ROWS)
    profile.add_argument("--sheet", default=None)
    profile.add_argument("--all-sheets", action="store_true", help="Profile every worksheet in an XLS/XLSX workbook.")
    profile.set_defaults(
        handler=lambda args: (
            profile_tabular_workbook_sheets(
                resolve_cli_path(args.path, args),
                max_sample_rows=args.max_sample_rows,
            )
            if args.all_sheets
            else profile_tabular_file(
                resolve_cli_path(args.path, args),
                max_sample_rows=args.max_sample_rows,
                sheet=args.sheet,
            )
        )
    )

    extract = tabular_subparsers.add_parser("extract", help="Extract tables into the shared SQLite cache.")
    extract.add_argument("path")
    extract.add_argument("--metadata-rows", type=int, default=MAX_METADATA_ROWS)
    extract.add_argument("--sheet", default=None)
    extract.set_defaults(
        handler=lambda args: extract_tabular_file(
            args.path,
            root_dir=resolve_cli_root(args),
            metadata_rows=args.metadata_rows,
            sheet=args.sheet,
        )
    )


def add_pdf_commands(subparsers: Any) -> None:
    """Add PDF inspection, preparation, and extraction-boundary commands."""
    pdf = subparsers.add_parser("pdf", help="Inspect or extract PDF files.")
    add_root_argument(pdf)
    pdf_subparsers = pdf.add_subparsers(dest="pdf_command", required=True)

    inspect = pdf_subparsers.add_parser("inspect", help="Inspect PDF text and optional page images.")
    inspect.add_argument("path")
    inspect.add_argument("--page-start", type=int, default=1)
    inspect.add_argument("--page-limit", type=int, default=DEFAULT_INSPECT_PAGE_LIMIT)
    inspect.add_argument("--max-text-chars", type=int, default=DEFAULT_INSPECT_TEXT_CHARS)
    inspect.add_argument("--include-images", action="store_true")
    inspect.add_argument("--dpi", type=int, default=96)
    inspect.set_defaults(
        handler=lambda args: inspect_pdf_file(
            resolve_cli_path(args.path, args),
            page_start=args.page_start,
            page_limit=args.page_limit,
            max_text_chars=args.max_text_chars,
            include_images=args.include_images,
            dpi=args.dpi,
        )
    )

    prepare = pdf_subparsers.add_parser("prepare", help="Render a PDF into a resumable artifact workspace.")
    prepare.add_argument("path")
    prepare.add_argument("--dpi", type=int, default=DEFAULT_DPI)
    prepare.add_argument("--max-pages", type=int, default=DEFAULT_MAX_PREPARE_PAGES)
    prepare.set_defaults(
        handler=lambda args: prepare_pdf_file(
            args.path,
            root_dir=resolve_cli_root(args),
            dpi=args.dpi,
            max_pages=args.max_pages,
        )
    )

    extract = pdf_subparsers.add_parser("extract", help="Extract PDF artifacts with narrow PyMuPDF-backed presets.")
    extract.add_argument("path")
    extract.add_argument("target", nargs="?", choices=["tables"], metavar="tables", help="Artifact kind to extract.")
    extract.add_argument(
        "preset",
        nargs="?",
        choices=sorted(PDF_TABLE_PRESET_MODES),
        metavar="{detected,coordinate,field-value,line-value}",
        help="PyMuPDF-backed extraction preset.",
    )
    extract.add_argument("--name", default=None)
    extract.add_argument("--number", type=int, default=1)
    extract.add_argument("--pages", default=None, help="Comma-separated 1-based pages, for example 1,3,5.")
    extract.add_argument("--page-start", type=int, default=None)
    extract.add_argument("--page-end", type=int, default=None)
    extract.add_argument("--include-page", action="store_true")
    extract.add_argument("--rules", default=None, help="YAML rules file for reusable PDF extraction options.")
    extract.add_argument("--skip-line", dest="skip_lines", action="append", default=[])
    extract.add_argument("--skip-prefix", dest="skip_prefixes", action="append", default=[])
    extract.add_argument("--stop-prefix", dest="stop_prefixes", action="append", default=[])
    extract.add_argument("--section", default=None, help="tables line-value/field-value: regex for section heading lines; stored as the section context.")
    extract.add_argument(
        "--context",
        action="append",
        default=[],
        help="tables line-value/field-value: FIELD=REGEX. Matching lines update a carried context column.",
    )
    extract.add_argument(
        "--clear-context",
        action="append",
        default=[],
        help="tables line-value/field-value: FIELD=REGEX. Matching lines clear a carried context column.",
    )
    extract.add_argument("--split-by", default=None, help="tables line-value/field-value/coordinate: write one CSV per distinct FIELD value.")
    extract.add_argument("--split-sections", action="store_true", help="tables line-value/field-value: shortcut for --split-by section.")
    extract.add_argument("--drop-empty-split", action="store_true", help="tables split outputs: omit rows where the split field is empty.")
    extract.add_argument("--output-columns", default=None, help="Comma-separated output columns.")
    extract.add_argument("--min-rows", type=int, default=1, help="tables detected: minimum detected rows.")
    extract.add_argument("--min-filled-cells", type=int, default=None, help="tables detected: minimum non-empty cells in a forced-column data row.")
    extract.add_argument("--strategy", choices=PDF_TABLE_STRATEGIES, default=None, help="tables detected: set both PyMuPDF table strategies.")
    extract.add_argument("--vertical-strategy", choices=PDF_TABLE_STRATEGIES, default=None, help="tables detected: PyMuPDF vertical strategy.")
    extract.add_argument("--horizontal-strategy", choices=PDF_TABLE_STRATEGIES, default=None, help="tables detected: PyMuPDF horizontal strategy.")
    extract.add_argument("--clip", default=None, help="tables detected: clip rectangle X0,Y0,X1,Y1 in PDF points.")
    extract.add_argument("--require-header", action="store_true", help="tables detected: skip tables without useful PyMuPDF header metadata.")
    extract.add_argument(
        "--merge-tables",
        choices=["auto", "always", "never"],
        default=None,
        help="tables detected: merge same-schema detected tables by continuation geometry, always, or never.",
    )
    extract.add_argument("--value-preset", choices=sorted(PDF_VALUE_PRESETS), default=None, help="tables line-value: built-in value regex preset.")
    extract.add_argument("--value-pattern", default=None, help="tables line-value: regex matching value lines.")
    extract.add_argument("--label-column", default=None, help="tables line-value: output column for label text.")
    extract.add_argument("--value-column", default=None, help="tables line-value/field-value: output column for extracted values.")
    extract.add_argument("--field-column", default=None, help="tables field-value: output column for field names.")
    extract.add_argument("--field", dest="fields", action="append", default=[], help="tables field-value: FIELD=OUTPUT mapping. Repeat for each field.")
    extract.add_argument("--collect-until-next-field", action="store_true", help="tables field-value: collect multiline values until another configured field.")
    extract.add_argument("--column", dest="columns", action="append", default=[], help="tables coordinate: NAME:X_MIN:X_MAX. Repeat for each column.")
    extract.add_argument("--y-min", type=float, default=0)
    extract.add_argument("--y-max", type=float, default=10_000)
    extract.add_argument("--y-tolerance", type=float, default=4)
    extract.add_argument("--anchor-y-slop", type=float, default=None)
    extract.add_argument("--required-columns", default=None, help="tables coordinate: comma-separated columns required for a row.")
    extract.add_argument("--continuation-column", default=None, help="tables coordinate: column whose wrapped text joins anchored rows.")
    extract.set_defaults(
        handler=lambda args: extract_pdf_file(
            resolve_cli_path(args.path, args),
            extraction=pdf_extract_spec_from_args(args),
            root_dir=resolve_cli_root(args),
        )
    )


def add_artifact_commands(subparsers: Any) -> None:
    """Add SQLite-backed artifact commands."""
    artifacts = subparsers.add_parser("artifacts", help="Inspect and query prepared artifacts.")
    add_root_argument(artifacts)
    artifacts.add_argument("--database-path", default=None, help="SQLite artifact database path. Relative paths resolve under --root-dir when provided.")
    artifact_subparsers = artifacts.add_subparsers(dest="artifact_command", required=True)

    query = artifact_subparsers.add_parser("query", help="Query prepared artifacts with read-only SQL. Prefix SQL with @ to read from a file.")
    query.add_argument("sql")
    query.add_argument("--max-rows", type=int, default=200)
    query.set_defaults(
        handler=lambda args: query_artifacts(
            root_dir=resolve_cli_root(args),
            sql=read_sql_argument(args.sql, root_dir=resolve_cli_root(args)),
            database_path=resolve_cli_database_path(args),
            max_rows=args.max_rows,
        )
    )

    save = artifact_subparsers.add_parser("save-view", help="Save an artifact query as a named SQLite view.")
    save.add_argument("view_name")
    save.add_argument("sql")
    save.add_argument("--replace", action="store_true")
    save.set_defaults(
        handler=lambda args: save_artifact_view(
            read_sql_argument(args.sql, root_dir=resolve_cli_root(args)),
            args.view_name,
            root_dir=resolve_cli_root(args),
            database_path=resolve_cli_database_path(args),
            sql_file_path=resolve_sql_argument_path(args.sql, root_dir=resolve_cli_root(args)),
            replace=args.replace,
        )
    )

    list_command = artifact_subparsers.add_parser("list", help="List queryable prepared artifacts.")
    list_command.add_argument("--include-internal", action="store_true")
    list_command.add_argument("--max-items", type=int, default=DEFAULT_CLI_ARTIFACT_LIST_LIMIT)
    list_command.add_argument("--detail", choices=sorted(SQL_ARTIFACT_LIST_DETAILS), default="compact")
    list_command.add_argument("--all", action="store_true", help="Return the full artifact catalog.")
    list_command.set_defaults(
        handler=lambda args: list_sql_artifacts(
            root_dir=resolve_cli_root(args),
            database_path=resolve_cli_database_path(args),
            include_internal=args.include_internal,
            max_items=None if args.all else args.max_items,
            detail=args.detail,
        )
    )

    from_source = artifact_subparsers.add_parser("from-source", help="List artifacts created from one source file.")
    from_source.add_argument("path")
    from_source.add_argument("--source-format", default=None)
    from_source.add_argument("--include-internal", action="store_true")
    from_source.set_defaults(
        handler=lambda args: artifacts_from_source(
            str(resolve_cli_path(args.path, args)),
            root_dir=resolve_cli_root(args),
            database_path=resolve_cli_database_path(args),
            include_internal=args.include_internal,
            source_format=args.source_format,
        )
    )

    suggest = artifact_subparsers.add_parser("suggest", help="Suggest queryable artifacts for a natural-language question.")
    suggest.add_argument("question")
    suggest.add_argument("--max-results", type=int, default=5)
    suggest.add_argument("--include-internal", action="store_true")
    suggest.set_defaults(
        handler=lambda args: suggest_sql_artifacts(
            args.question,
            root_dir=resolve_cli_root(args),
            database_path=resolve_cli_database_path(args),
            include_internal=args.include_internal,
            max_results=args.max_results,
        )
    )

    describe = artifact_subparsers.add_parser("describe", help="Describe one queryable artifact.")
    describe.add_argument("name")
    describe.add_argument("--sample-rows", type=int, default=10)
    describe.add_argument("--text-value-hints", type=int, default=3)
    describe.set_defaults(
        handler=lambda args: describe_sql_artifact(
            args.name,
            root_dir=resolve_cli_root(args),
            database_path=resolve_cli_database_path(args),
            sample_rows=args.sample_rows,
            text_value_hints=args.text_value_hints,
        )
    )


def add_email_commands(subparsers: Any) -> None:
    """Add email reference inspection commands."""
    email = subparsers.add_parser("email", help="Inspect EML/MSG reference emails.")
    add_root_argument(email)
    email_subparsers = email.add_subparsers(dest="email_command", required=True)

    inspect = email_subparsers.add_parser("inspect", help="Inspect an email as reference context.")
    inspect.add_argument("path")
    inspect.add_argument("--max-body-chars", type=int, default=2000)
    inspect.set_defaults(
        handler=lambda args: inspect_email_file(
            resolve_cli_path(args.path, args),
            max_body_chars=args.max_body_chars,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the Tabuflow command parser."""
    parser = argparse.ArgumentParser(
        prog="tabuflow",
        description="Inspect tabular/PDF/email sources and query prepared artifacts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_tabular_commands(subparsers)
    add_pdf_commands(subparsers)
    add_email_commands(subparsers)
    add_artifact_commands(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the Tabuflow CLI."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = args.handler(args)
    except Exception as exc:
        print_json(
            {
                "status": "error",
                "error_type": exc.__class__.__name__,
                "message": str(exc),
            }
        )
        return 1
    print_json(payload)
    return 1 if isinstance(payload, dict) and payload.get("status") == "error" else 0


if __name__ == "__main__":
    sys.exit(main())
