---
name: gcp-cost-pipeline
description: Use when transforming monthly GCP billing exports into reconciliation, IBS charge-item upload rows, or historical management-summary CSV/workbook outputs.
---

# GCP Cost Pipeline

Use this skill for the GCP-only billing workflow.

The target is a specific transformation from one monthly GCP cost table into two business outputs:

- an aggregated GCP result for review and reconciliation,
- an IBS charge-item upload output.

Reference workbooks can be used to understand expected shape, naming intent, rates, and the historical manual process. They are not additional runtime inputs unless the user explicitly changes the target, such as asking for a CSV in the same shape as a historical "GCP Rev and Cost" monthly summary.

## Input

Primary input:

- `cost_table.xlsx`: the monthly raw GCP cost table export.

CSV copies of the same export are acceptable as local fixtures, but the production input is the cost-table content in Excel form.

Treat the raw cost table as the only required source input for this skill. If an output needs customer metadata that is not present in the cost table, that mapping must come from maintained implementation/configuration, not from asking the user for extra files as part of the default workflow.

## Raw Cost Table Boundary

The GCP export headers are stable but messy. Treat them as ingestion-boundary labels only. Normalize them into semantic internal fields before applying business logic, and do not let raw labels leak into derived output names.

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

Use `references/gcp-cost-view-contract.md` for the semantic field mapping, formula definitions, and output contracts.

## Outputs

Write workflow artifacts under the current working directory's `artifacts/` path, not beside source fixtures:

- reusable SQL or scratch transformation files: `artifacts/sql/`
- final review/upload files: `artifacts/outputs/`
- prepared tabular data: `artifacts/tabular.sqlite`

Do not write generated workbooks into `examples/gcp/` or overwrite source and reference files there. If a one-off script is needed, keep it artifact-owned under `artifacts/sql/` or promote the logic into maintained repo code.

Run Tabuflow from the project root. Do not pass workspace/output directory arguments to Tabuflow tools.

Output 1: aggregated GCP result.

- Purpose: reconcile the monthly cost table by account, category, and summary.
- Shape: semantic result rows, not workbook-mirrored pivot labels.
- Expected levels: summary, category, and account/customer.
- Expected metrics: usage before reseller margin, net GCP cost, discount, customer charge or revenue, HKD revenue, gross profit, gross profit percent, and row/item count.
- Reference workbooks may be used for shape and reconciliation intent.

Output 2: IBS charge-item upload.

- Purpose: produce the charge rows needed by the IBS upload template.
- Use the IBS upload template as the reference for the charge-item output shape when it is available.
- Core output sheet: `Bill Item`.
- Target fidelity: almost 1-to-1 with the fixed IBS template. Preserve the column order, constants, date format, and total row unless the user explicitly asks for a different export shape.
- Important columns visible in the reference: an unlabeled customer-name column, `bill_acct`, `contct_id`, `chrg_code`, `chrg_amt`, `start_bill`, `end_bill`, `remark1`, `remark2`, `remark3`, `remark4`, `bill_date`, `ccc`, `bill_methd`, and `uploaded`.
- `chrg_amt` is the HKD customer charge for the account.
- IBS-only fields should be filled from maintained mapping/defaults when they are not present in the raw cost table. Use maintained customer mapping and template constants rather than stopping on missing raw columns.
- Maintain customer mapping as part of the implementation/configuration used by the run; do not ask the user for an extra spreadsheet as a normal workflow input.
- Fixed template constants: `chrg_code = DY80`, `ccc = CAM8`, `bill_methd = O`, and `uploaded = N`.
- `start_bill`, `end_bill`, and `bill_date` should be numeric-looking `YYYYMMDD` values like the reference workbook.
- Include the IBS total row with `Total Rec` and `Total Amt`.
- Remarks carry human-facing context such as `GCP Usage Consumption`, USD service charge, discount, exchange rate, billing account ID, or project name.

Output 3: management-summary CSV or workbook.

- Purpose: recreate the accountant-facing monthly summary view when the user asks for a "similar format", "monthly summary", "GCP Rev and Cost" output, or a CSV demo of that workbook.
- Use the historical summary workbook as a reference for the stacked month/customer/account/charge-type header and forecast block when it is available.
- Use per-account monthly cost-report workbooks for the service-charge and credits split when those reports are provided. The raw GCP export can validate net totals, but it may not preserve the same service-vs-credit presentation buckets.
- Output should be a reviewable CSV or workbook under `artifacts/outputs/`, with a recognizable name such as `gcp_<month>_summary_demo.csv` or `gcp_<month>_management_summary.csv`.
- Keep the output source-backed: name the source workbook/report used for each account, show the billing account IDs, and do not hide the rate assumptions.
- For the detailed contract and formulas, use `references/gcp-management-summary-csv.md`.

## Process

1. Inspect the raw cost table shape.
   - Confirm the stable GCP export columns are present.
   - Recover invoice metadata from rows above the main header when available.
   - Do not infer business field names from workbook pivot labels.

2. Load the cost table into a queryable tabular layer.
   - The workflow is mostly filtering, grouping, joining, and deriving stable output rows.
   - Prefer `tabuflow tabular extract ...` or the equivalent MCP tool so the raw table is represented in `artifacts/tabular.sqlite`.
   - After extraction, use `tabuflow artifacts from-source`, `describe`, and bounded `query` calls to discover and validate the artifact rather than copying generated table names from memory.
   - Any additional dataframe/script implementation is acceptable only if the same semantics are inspectable and the reusable work files stay under `artifacts/`.

3. Normalize raw columns into semantic fields.
   - Map the exact raw headers into names such as `account_id`, `account_name`, `credit_type`, `cost_type`, `unrounded_cost_usd`, and `cost_usd`.
   - Keep raw names isolated to the ingestion step.
   - Treat missing expected raw columns as a schema mismatch, not as a reason to guess new aliases.

4. Derive account-level business rows.
   - Group by billing account.
   - Compute usage before reseller margin, net GCP cost, customer charge, customer charge HKD, gross profit, gross profit percent, and item count.
   - Apply discounts and categories from maintained business rules when those rules exist in code/configuration.

5. Produce both outputs.
   - The aggregated result should expose summary, category, and account/customer rows with semantic columns.
   - The IBS result should expose `Bill Item` rows in the upload-template shape.
   - Produce a reviewable aggregated file and a template-like IBS upload workbook or CSV under `artifacts/outputs/`. The exact implementation can vary, but the output contract and artifact location cannot.
   - Use recognizable names such as `aggregated_cost_table.xlsx` and `IBS_ChargeItemUpload_GCP.xlsx` inside `artifacts/outputs/`.

6. If a management-summary CSV is requested, map the selected month block before generating it.
   - Identify the selected month from the summary workbook headers or the user's requested billing month; do not hardcode February or fixed columns.
   - Map each month column by customer name, billing account ID, and charge type.
   - Pull the service-charge and savings split from matching monthly cost-report workbooks when they are available.
   - Pull reseller/program discount from normalized raw cost rows where `credit_type` is `RESELLER_MARGIN`, using unrounded cost.
   - Preserve explicit rates: the invoice column's HKD billed amount uses the month cost rate, cost-transfer columns use the revenue rate, and HKD GCP cost uses the month cost rate.
   - Include a forecast block only when the reference summary has one or the user asks for it.

7. Validate before treating the output as done.
   - Aggregated totals should reconcile to the raw cost table after footer rows such as `Rounding error` and `Total` are excluded.
   - HKD amounts should use an explicit HKD-per-USD business rate; do not treat a generic GCP invoice `currency_exchange_rate` as the HKD rate unless that is explicitly known for the source.
   - IBS row counts and amounts should match the accounts that should be billed.
   - IBS output should have the exact template header order, one total row, and template constants populated on every bill-item row.
   - Management-summary CSV totals should reconcile across USD service, credits, billed amount, reseller/program discount, USD GCP cost, HKD cost, HKD billed amount, and forecast rows.
   - Report cent-level differences between cost-report subtotals and reference summary cells instead of silently choosing one.

## Rules

- Do not hardcode a generated table name, extracted table hash, or billing month.
- Do not require companion spreadsheets or config CSVs as default user inputs.
- Do not mirror accountant workbook labels exactly; use semantic names that match the data meaning.
- Keep formulas in an inspectable tabular/query layer, not hidden in workbook cells.
- Keep local file paths out of user-facing output columns.
- If an IBS row cannot be produced because no maintained customer mapping exists, skip that IBS row and report the skipped account. Do not treat template constants such as `DY80`, `CAM8`, `O`, or `N` as blockers.
- Prefer a recognizable IBS upload artifact over a perfectly generalized billing engine. Template defaults are fine when the output is validated.
- For historical management summaries, do not derive the service-charge versus credits split from the raw GCP export alone when monthly cost-report workbooks are available; use the reports for that presentation split and the raw export for reseller-margin validation.

## Prompt Frame

Use this framing when building or reviewing the GCP workflow:

```text
Transform one monthly GCP cost table into two outputs: an aggregated reconciliation result and IBS charge-item upload rows.

Treat the GCP raw headers as stable ingestion labels, then normalize them into semantic fields before applying business logic.

Use an inspectable tabular transformation. Do not depend on workbook pivot labels, hidden cell formulas, generated table names, or extra runtime input files.
```

For a historical management-summary CSV, extend the frame:

```text
Recreate the selected month summary in the reference workbook's visible shape.

Use the monthly customer cost reports for service-charge and savings buckets, use the raw GCP export for RESELLER_MARGIN/program discount validation, preserve the explicit HKD rates, and write the CSV under artifacts/outputs/.
```
