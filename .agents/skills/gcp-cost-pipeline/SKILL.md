---
name: gcp-cost-pipeline
description: Use when transforming a monthly GCP cost table into aggregated reconciliation results and IBS charge-item upload rows. Normalize the stable but messy raw GCP export headers at ingestion, then derive semantic account, category, summary, and IBS outputs without relying on workbook labels or hardcoded table names.
---

# GCP Cost Pipeline

Use this skill for the GCP-only billing workflow.

The target is a specific transformation from one monthly GCP cost table into two
business outputs:

- an aggregated GCP result for review and reconciliation,
- an IBS charge-item upload output.

The example workbooks under `examples/gcp/` are references for expected shape,
naming intent, rates, and the historical manual process. They are not additional
runtime inputs unless the user explicitly changes the target.

## Input

Primary input:

- `cost_table.xlsx`: the monthly raw GCP cost table export.

The current example fixture is `examples/gcp/cost_table.csv`, but the intended
production input is the same cost-table content in Excel form.

Treat the raw cost table as the only required source input for this skill. If an
output needs customer metadata that is not present in the cost table, that
mapping must come from maintained implementation/configuration, not from asking
the user for extra files as part of the default workflow.

## Raw Cost Table Boundary

The GCP export headers are stable but messy. Treat them as ingestion-boundary
labels only. Normalize them into semantic internal fields before applying
business logic, and do not let raw labels leak into derived output names.

Known raw cost-table columns:

- `Billing account name`
- `Billing account ID`
- `Project name`
- `Project ID`
- `Project hierarchy`
- `Service description`
- `Service ID`
- `SKU description`
- `SKU ID`
- `Consumption model description`
- `Credit type`
- `Cost type`
- `Usage start date`
- `Usage end date`
- `Usage amount`
- `Usage unit`
- `Unrounded Cost ($)`
- `Cost ($)`

Use `references/gcp-cost-view-contract.md` for the semantic field mapping,
formula definitions, and output contracts.

## Outputs

Output 1: aggregated GCP result.

- Purpose: reconcile the monthly cost table by account, category, and summary.
- Shape: semantic result rows, not workbook-mirrored pivot labels.
- Expected levels: summary, category, and account/customer.
- Expected metrics: usage before reseller margin, net GCP cost, discount,
  customer charge or revenue, HKD revenue, gross profit, gross profit percent,
  and row/item count.
- Reference examples: `examples/gcp/aggregated_cost_table.xlsx` and
  `examples/gcp/aggregated_cost_table2.xlsx`.

Output 2: IBS charge-item upload.

- Purpose: produce the charge rows needed by the IBS upload template.
- Reference example: `examples/gcp/IBS_ChargeItemUploadTemplate_Cloud_GCP_20260312.xls`.
- Core output sheet: `Bill Item`.
- Important columns visible in the reference: `bill_acct`, `contct_id`,
  `chrg_code`, `chrg_amt`, `start_bill`, `end_bill`, `remark1`, `remark2`,
  `remark3`, `remark4`, `bill_date`, `ccc`, `bill_methd`, and `uploaded`.
- `chrg_amt` is the HKD customer charge for the account.
- Remarks carry human-facing context such as `GCP Usage Consumption`, USD
  service charge, discount, exchange rate, billing account ID, or project name.

## Process

1. Inspect the raw cost table shape.
   - Confirm the stable GCP export columns are present.
   - Recover invoice metadata from rows above the main header when available.
   - Do not infer business field names from workbook pivot labels.

2. Load the cost table into a queryable tabular layer.
   - The workflow is mostly filtering, grouping, joining, and deriving stable
     output rows.
   - Any implementation is acceptable if it preserves the same semantics and is
     inspectable enough to validate.

3. Normalize raw columns into semantic fields.
   - Map the exact raw headers into names such as `account_id`, `account_name`,
     `credit_type`, `cost_type`, `unrounded_cost_usd`, and `cost_usd`.
   - Keep raw names isolated to the ingestion step.
   - Treat missing expected raw columns as a schema mismatch, not as a reason to
     guess new aliases.

4. Derive account-level business rows.
   - Group by billing account.
   - Compute usage before reseller margin, net GCP cost, customer charge,
     customer charge HKD, gross profit, gross profit percent, and item count.
   - Apply discounts and categories from maintained business rules when those
     rules exist in code/configuration.

5. Produce both outputs.
   - The aggregated result should expose summary, category, and account/customer
     rows with semantic columns.
   - The IBS result should expose `Bill Item` rows in the upload-template shape.

6. Validate before treating the output as done.
   - Aggregated totals should reconcile to the raw cost table after footer rows
     such as `Rounding error` and `Total` are excluded.
   - HKD amounts should use an explicit HKD-per-USD business rate; do not treat a
     generic GCP invoice `currency_exchange_rate` as the HKD rate unless that is
     explicitly known for the source.
   - IBS row counts and amounts should match the accounts that should be billed.

## Rules

- Do not hardcode a generated table name, extracted table hash, or billing month.
- Do not require companion spreadsheets or config CSVs as default user inputs.
- Do not mirror accountant workbook labels exactly; use semantic names that
  match the data meaning.
- Keep formulas in an inspectable tabular/query layer, not hidden in workbook
  cells.
- Keep local file paths out of user-facing output columns.
- If an IBS-required field such as `bill_acct` or `contct_id` cannot be derived
  from the raw cost table or maintained mapping, report the mapping gap clearly.

## Prompt Frame

Use this framing when building or reviewing the GCP workflow:

```text
Transform one monthly GCP cost table into two outputs: an aggregated
reconciliation result and IBS charge-item upload rows.

Treat the GCP raw headers as stable ingestion labels, then normalize them into
semantic fields before applying business logic.

Use an inspectable tabular transformation. Do not depend on workbook pivot
labels, hidden cell formulas, generated table names, or extra runtime input
files.
```
