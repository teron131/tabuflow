---
name: billing-tabular-pipeline
description: Use when analyzing, summarizing, or designing repeatable outputs from billing CSV, XLS, or XLSX files that may have messy headers, metadata rows, multiple tables, or month-to-month layout drift.
---

# Billing Tabular Pipeline

## Use

Use this skill for accountant-style billing exports that should keep working when a new month arrives.

This is an outcome-first skill. It defines the correctness bar for billing tables; it does not require a particular app tool, command, or storage engine.

For Tabuflow-backed runs, keep generated work under the current working directory's `artifacts/` directory: extracted tabular data in `artifacts/tabular.sqlite`, reusable SQL or scratch transformation files in `artifacts/sql/`, and final validated CSV/XLSX outputs in `artifacts/outputs/`. Treat source/example directories as read-only inputs unless the user explicitly asks to edit them.

## Tools

Use Tabuflow for messy source discovery and artifact-backed validation:

- `tabuflow tabular inspect <file>` for a bounded view of headers, metadata, sheets, and rows.
- `tabuflow tabular profile <file>` when header rows, multiple regions, or sheet structure are unclear.
- `tabuflow tabular extract <file>` once the likely table shape is understood.
- `tabuflow artifacts list`, `from-source`, `describe`, and `query` to find, inspect, and validate extracted data.

Use ordinary shell/editor tools for writing SQL, small transformation scripts, markdown notes, or final reports. Keep reusable SQL/scripts in `artifacts/sql/` and final outputs in `artifacts/outputs/`.

## Inputs

Start from the files the user, task, or test case provides. Do not assume companion workbooks, templates, mappings, hidden expected outputs, or prior generated files exist unless they are visible or maintained by the implementation.

Inspect source files as unknown until their actual structure is proven. Check for pre-header metadata, blank spacer rows, repeated headers, footer totals, hidden summaries, multiple tables, merged-looking headers, unusual date/rate fields, account IDs, invoice numbers, currencies, and report-period metadata.

Identify file roles by content, not filenames: raw export, reference workbook, customer/report workbook, upload template, or generated output candidate.

When a target field depends on metadata outside the main table body, recover it from visible metadata rows when possible. If it is not available, report that gap instead of inventing a value.

## Targets

Define the business target before transforming. Common billing targets are:

- a summary or management view,
- a reconciliation table,
- customer/account rows,
- upload-template rows,
- a review table,
- a saved query/view for later work.

Separate source labels from semantic output names. Literal source headers belong at the ingestion boundary; derived outputs should use names that describe business meaning unless the user explicitly asks to preserve the original shape.

## Workflow

1. Inspect a bounded raw window before extracting or transforming. Do not assume row 1 is the header.
2. Identify the real table structure: header rows, metadata rows, repeated headers, footers, totals, blank regions, and any multiple-table layout.
3. Normalize at the ingestion boundary. Convert raw columns into stable semantic fields before grouping, filtering, joining, or deriving formulas.
4. Build from an inspectable tabular layer. Use SQL, dataframe logic, or another tabular method when it makes the transformation easier to inspect and validate.
5. When Tabuflow is available, extract the source into the artifact store before writing business-output scripts, then rediscover the table with artifact listing, source lookup, description, and bounded query tools.
6. Keep non-trivial SQL or scratch transformations in `artifacts/sql/`, and write final review/upload outputs under `artifacts/outputs/`.

## Validate

Reconcile totals back to the current source file. Confirm row counts and grouped totals are plausible. Check that footer rows, repeated headers, and visible total rows were not double-counted as business data.

If multiple source files or months are present, scope the result to the current source unless the user asks for a cross-source result. If a visible source cannot support a requested split, rate, mapping, or template field, keep the output honest and report the missing basis.

## Boundaries

- Do not hardcode generated table names, content hashes, billing months, header row numbers, or fixed row counts.
- Do not depend on one month's filenames or worksheet positions; derive period and shape from visible content.
- Do not split CSV rows manually when a structured reader is available.
- Do not invent merged headers or business fields from ambiguous labels.
- Do not hide formulas inside workbook cells when an inspectable query/script layer is possible.
- Do not write generated outputs beside source fixtures unless the user asks.
- Use `gcp-cost-pipeline` for GCP cost-table outputs.
- Use `aws-invoice-pdf-tables` for AWS invoice PDF visual table cleanup.
