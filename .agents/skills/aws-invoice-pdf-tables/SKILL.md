---
name: aws-invoice-pdf-tables
description: Use when converting AWS invoice PDF table extraction into SQLite-ready tables. Treat OCR/visual table fragments as authoritative for final table boundaries, hierarchy, and row order; use LangExtract text only to enrich or validate semantics.
---

# AWS Invoice PDF Tables

Use this after OCR or LangExtract has produced table-like fragments from an AWS invoice PDF.

The visual/OCR table structure is the primary source of truth. Preserve visual table boundaries, visual table titles, row order, parent/child hierarchy, and totals unless the caller explicitly asks for logical regrouping.

LangExtract text spans can help recover labels, amounts, account numbers, dates, and other semantic fields, but they must not override OCR/visual evidence for table boundaries or hierarchy.

## Final Tables

Default final output: one SQLite-ready table per meaningful visual invoice table, using the visual table title as the source table name. Common visual tables include:

- `Summary`
- `Detail for Consolidated Bill`
- `Activity By Account`
- `Summary for Linked Account`
- `Detail for Linked Account`

Rows should be immediately importable into SQLite. For two-column AWS billing tables, use these columns unless the table visibly requires a richer shape:

- `label`
- `amount`
- `row_role`
- `parent_label`

Use `row_role` values:

- `parent`: top-level charge category, service, account, or linked account row.
- `child`: visually indented row under the nearest parent row.
- `total`: highlighted or subtotal/final-total row.

Set `parent_label` for child rows to the nearest preceding parent row in the same visual table. Leave it empty for parent rows and standalone totals unless the visual table clearly nests the total under a parent.

## Workflow

1. Use OCR/visual table output as the authoritative table-boundary evidence.
2. Use LangExtract output as a semantic baseline for labels, amounts, dates, and account/invoice facts.
3. Reconcile the two in the fixer:
   - OCR/visual wins for table names, table boundaries, row order, indentation, parent/child hierarchy, and totals.
   - LangExtract can fill or validate text values when OCR text is ambiguous.
4. Produce a single final SQLite-ready payload with `path`, `format`, and `tables`.
5. Keep provenance metadata out of importable table columns. If metadata is needed internally, keep it in graph state rather than final rows.
6. Validate before writing SQLite:
   - output visual table count should match meaningful OCR table count,
   - output row count should stay close to OCR table rows,
   - no invoice header facts should leak into final table rows unless they appear inside OCR table rows.

Do not create one final table per page, OCR chunk, or service unless that is the actual visual table structure.

## Visual Table Preparation Prompt

Use this as the system/fixer prompt for the final visual-table preparation step:

```text
Prepare final SQLite-ready visual tables from AWS billing PDF table fragments.

Use OCR/visual table fragments as the source of truth for table titles, table boundaries, row order, indentation, row hierarchy, and totals. Use LangExtract rows or plain PDF text only to enrich or validate text values.

Rules:
- Return final DB-ready tables that preserve meaningful visual table boundaries.
- Use visual table titles as output table names.
- Do not create one output table per page, service, or OCR chunk unless the visual PDF really has that table.
- Preserve every meaningful source row.
- Do not summarize, aggregate, pivot, or collapse repeated service/tax/discount rows.
- The total output row count should stay close to the input row count, except for duplicated headers, empty rows, and exact duplicated continuation artifacts.
- For two-column billing tables, use columns: label, amount, row_role, parent_label.
- Interpret visual hierarchy:
  - parent = top-level service/category/account row;
  - child = visually indented row under the nearest parent;
  - total = visually highlighted subtotal/final-total row.
- Preserve zero-amount rows such as Tax, VAT, GST, CT, and estimated sales tax.
- Do not promote invoice header facts such as TOTAL AMOUNT into table rows unless they appear in OCR table fragments.
- Preserve exact words, signs, currency symbols, commas, decimals, and row order within each logical table.
- Keep all output cells as strings.
- Do not invent values; use empty strings for unknown hierarchy fields.
```

## Optional Logical Regrouping

Only if the caller explicitly asks for logical billing tables, transform visual tables into these logical names:

- `invoice_summary`
- `charge_details`
- `account_activity`
- `payment_details`

When doing this optional regrouping, preserve every meaningful visual row and keep row count close to the source. Do not use logical regrouping as the default.

## Validation

Before writing final SQLite:

- Count input rows across OCR visual tables.
- Count output rows across final tables.
- Reject output if invoice header facts appear as extra table rows without OCR-table evidence.
- Reject output if provenance fields such as `source_text`, `char_interval`, page coordinates, or confidence appear as importable DB columns.
- For invoices with at least 100 input rows, fail if output rows are less than 75% of input rows.
- If the model collapses hundreds of rows into a small summary, reject the output and rerun with stricter row-preservation instructions or smaller grouped inputs.
