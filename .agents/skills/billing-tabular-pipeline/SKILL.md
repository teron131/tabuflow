---
name: billing-tabular-pipeline
description: Use when analyzing, summarizing, or designing repeatable outputs from billing CSV, XLS, or XLSX files that may have messy headers, metadata rows, multiple tables, or month-to-month layout drift.
---

# Billing Tabular Pipeline

Use this skill for accountant-style billing exports that should keep working when a new month arrives.

This is an outcome-first skill. It defines the correctness bar for billing tables; it does not require a particular app tool, command, or storage engine.

## Goal

Turn a billing spreadsheet or CSV into reliable business outputs:

- identify the real table structure,
- preserve useful invoice or report metadata,
- normalize messy source columns into semantic fields,
- derive the requested totals, reconciliations, upload rows, or review tables,
- validate the result against the source before treating it as done.

## Process

1. Understand the file shape first.
   - Do not assume row 1 is the header.
   - Check for pre-header metadata, blank spacer rows, repeated headers, footer totals, hidden summaries, and multiple tables.
   - Treat source files as unknown until their actual structure is inspected.

2. Define the target output before transforming.
   - Name the business output the user needs: summary, reconciliation table, customer/account rows, upload template rows, or another deliverable.
   - Identify required fields and formulas.
   - Separate source labels from semantic output names.

3. Normalize at the ingestion boundary.
   - Keep literal source headers only where the file is read.
   - Convert raw columns into stable semantic fields before grouping, filtering, joining, or deriving formulas.
   - Keep rows above the real header as metadata context when they carry invoice numbers, dates, currency, exchange rates, account IDs, or totals.

4. Build from an inspectable tabular layer.
   - Use SQL, dataframe logic, or another tabular method when it makes the transformation easier to inspect and validate.
   - Avoid manual spreadsheet reasoning for non-trivial transformations.
   - Prefer explicit intermediate semantic fields over hidden workbook formulas.

5. Validate the output.
   - Reconcile totals back to the current source file.
   - Confirm row counts and grouped totals are plausible.
   - Check that footer rows and repeated headers were not counted as business data.
   - If multiple source files or months are present, scope the result to the current source unless the user asks for a cross-source result.

## Rules

- Do not hardcode generated table names, content hashes, billing months, header row numbers, or fixed row counts.
- Do not split CSV rows manually when a structured reader is available.
- Do not invent merged headers or business fields from ambiguous labels.
- Do not let raw source column names leak into final derived output names unless the user explicitly wants the original file shape.
- When a target field depends on metadata outside the main table body, recover it from the metadata rows or report that it is unavailable.

## When To Reach For Provider-Specific Skills

- For GCP cost-table outputs, load `gcp-cost-pipeline`.
- For AWS invoice PDF visual table cleanup, load `aws-invoice-pdf-tables`.
- For a new provider-specific billing contract, keep this generic workflow and add only the provider-specific formulas and output rules on top.
