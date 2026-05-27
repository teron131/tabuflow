---
name: aws-invoice-pdf-tables
description: Use when extracting AWS invoice PDF billing tables into tabular data, especially when the final accounting output is unclear but coherent label/amount rows, hierarchy, totals, and OCR handoff decisions are needed.
---

# AWS Invoice PDF Tables

Use this for the minimum useful AWS invoice PDF step: recover coherent billing tables as tabular data. The final accounting result may be unknown; usable tables are still enough to move the workflow forward.

If the invoice folder includes email files, read `references/aws-email-reporting-context.md` for how to use those emails as supporting references. Do not turn emails into CSV/table outputs unless the caller explicitly asks for email reconciliation data.

The first pass should try direct PDF text extraction. Most AWS invoices are text PDFs where label/amount rows can be recovered without OCR. Use OCR or visual table extraction when pages have no text, direct extraction returns too few rows, or visual structure is needed to resolve ambiguous boundaries.

For AWS invoices, do not equate "table extraction" with literal PDF grid detection. Many AWS billing tables are stacked label/value blocks where the meaningful business columns are implied by repeated labels such as Charges, Discount, Credits, Tax, VAT, Total, or service names. The tool should recover the complete row stream; the agent should then decide whether a SQL/Python reshape is needed to make the useful business table.

Preserve table boundaries, titles, row order, parent/child hierarchy, and totals unless the caller explicitly asks for logical regrouping or the accounting output clearly requires a semantic reshape.

## Email References

When invoice folders include `.eml` or `.msg` files, inspect them as references before final reporting:

```bash
tabuflow email inspect <message.eml>
tabuflow email inspect <message.msg>
```

Use the result's subject, sender/date, attachments, and body preview to understand approvals and package grouping. Infer message type, provider, account, period, and amount from the email text when useful, but keep `reference_only: true` emails separate from PDF table outputs.

## First-Pass Text Extraction

Use Tabuflow's generic PyMuPDF line/value extractor for text-based AWS invoice PDFs. This is a domain skill command shape, not generic tool logic. It extracts text-derived label/amount rows into section-sized CSV tables with these columns:

- `section`
- `account`
- `label`
- `amount`
- `page`

Run shape from the repo root:

```bash
tabuflow pdf inspect <invoice.pdf>
tabuflow pdf extract <invoice.pdf> tables line-value \
  --name amount_lines \
  --value-preset money \
  --label-column label \
  --value-column amount \
  --section '^(Summary|Detail for Consolidated Bill|Activity By Account|Summary for Linked Account|Detail for Linked Account)$' \
  --context 'account=^(?P<value>.+ \([0-9]{12}\))$' \
  --clear-context 'account=^(Summary|Detail for Consolidated Bill|Activity By Account)$' \
  --split-sections \
  --drop-empty-split \
  --include-page
```

If a specific AWS invoice family needs reusable cleanup rules, use the optional sidecar:

```bash
tabuflow pdf extract <invoice.pdf> --rules .agents/skills/aws-invoice-pdf-tables/rules/aws_invoice_text.yaml
```

Expected common section outputs:

- `amount_lines_summary`
- `amount_lines_detail_for_consolidated_bill`
- `amount_lines_activity_by_account`
- `amount_lines_summary_for_linked_account`
- `amount_lines_detail_for_linked_account`

These are direct PDF text-layer tables, not visually verified OCR tables. Treat missing sections, obviously tiny row counts, or missing text as the handoff point to visual inspection/OCR, not as permission to invent rows.

## Stacked Billing Columns

Treat the `line-value` output as the canonical intermediate table, not always as the final table. It is good when the PDF visually stacks amounts like this:

- service or account heading,
- Charges,
- Discounts,
- Credits,
- Tax or VAT,
- Total,
- repeated again for the next service/account.

When the user needs an accounting-ready result, reshape this row stream after extraction:

- Use `section`, `account`, page order, and nearest parent labels to define the entity for each group.
- Promote repeated billing labels into columns only after checking they repeat in the same pattern within a section or parent group.
- Keep signs exactly as extracted; do not flip discounts or credits unless the source sign and target output contract require it.
- Preserve zero-value tax rows and explicit total rows.
- Keep the raw extracted CSVs as evidence; write the semantic reshape as SQL or a small script artifact, then validate it against the source totals.
- If the PDF has a literal grid, `tables detected` may help inspect it, but AWS invoice totals are usually more reliable through `tables line-value` plus an explicit reshape.

Common semantic columns may include `charges`, `discount`, `credits`, `tax`, `vat`, and `total`, but do not hardcode them as universal. Derive columns from the actual labels in the invoice and keep unknown labels as rows until the grouping is clear.

## Final Tables

Default final output: SQLite-ready tables from meaningful invoice tables or a validated semantic reshape of the first-pass amount lines. Common AWS invoice sections include:

- `Summary`
- `Detail for Consolidated Bill`
- `Activity By Account`
- `Summary for Linked Account`
- `Detail for Linked Account`

Rows should be immediately importable into SQLite. For raw two-column AWS billing tables, use these columns unless the table visibly requires a richer shape:

- `label`
- `amount`
- `row_role`
- `parent_label`

Use `row_role` values:

- `parent`: top-level charge category, service, account, or linked account row.
- `child`: visually indented row under the nearest parent row.
- `total`: highlighted or subtotal/final-total row.

Set `parent_label` for child rows to the nearest preceding parent row in the same visual table. Leave it empty for parent rows and standalone totals unless the visual table clearly nests the total under a parent.

For semantic reshapes, prefer explicit business columns plus enough grouping columns to trace the source, such as `section`, `account`, `service`, or `parent_label`. Do not leave the user with only raw `label`/`amount` rows when the requested deliverable is clearly a columnar accounting table.

## Workflow

1. Inspect the PDF text and page count.
2. If emails are present, inspect them as supporting references and keep notes separate from PDF table outputs.
3. Run direct text extraction for AWS invoice-style text PDFs.
4. Review text-derived amount-row counts, page summaries, section names, and a sample of extracted rows.
5. Decide whether the raw amount-line table is enough or whether stacked labels need a SQL/Python semantic reshape into columns.
6. Escalate pages to OCR/visual extraction only when direct text extraction is empty, obviously incomplete, or visually ambiguous.
7. If OCR/visual output is used, visual evidence wins for table names, table boundaries, row order, indentation, parent/child hierarchy, and totals.
8. Produce SQLite-ready tabular data with stable columns.
9. Keep provenance metadata out of importable table columns. If metadata is needed internally, keep it in sidecar JSON or metadata tables.
10. Validate:
   - text-derived amount-row count is plausible for the PDF page count and invoice type,
   - summary/detail rows are not collapsed into one total,
   - repeated labels that became columns reconcile back to the raw amount-line rows,
   - invoice header facts do not leak into table rows unless they appear inside a table,
   - zero-amount tax rows are preserved,
   - scanned pages are reported as needing OCR,
   - email-reported account IDs, periods, and amounts are treated as reference hints, not as replacements for PDF table rows or default CSV outputs.

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
