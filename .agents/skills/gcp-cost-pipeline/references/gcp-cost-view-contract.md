# GCP Cost Output Contract

Use this reference when a task needs the GCP cost-table field mapping, formulas, or output shape.

The source input is one monthly GCP cost table, conceptually `cost_table.xlsx`. CSV copies of the same export can be used for local validation.

## Raw Column Mapping

Normalize the raw GCP headers into semantic fields at ingestion:

- `Billing account name -> account_name`
- `Billing account ID -> account_id`
- `Project name -> project_name`
- `Project ID -> project_id`
- `Project hierarchy -> project_hierarchy`
- `Service description -> service_description`
- `Service ID -> service_id`
- `SKU description -> sku_description`
- `SKU ID -> sku_id`
- `Consumption model description -> consumption_model_description`
- `Credit type -> credit_type`
- `Cost type -> cost_type`
- `Usage start date -> usage_start_date`
- `Usage end date -> usage_end_date`
- `Usage amount -> usage_amount`
- `Usage unit -> usage_unit`
- `Unrounded Cost ($) -> unrounded_cost_usd`
- `Cost ($) -> rounded_cost_usd`

Use the raw names only at this boundary. Downstream logic should use semantic fields.

## Invoice Metadata

When present in rows above the real header, recover:

- `invoice_number`
- `invoice_date`
- `due_date`
- `billing_id`
- `billing_account_id`
- `currency`
- `currency_exchange_rate`
- `total_amount_due`

Keep invoice metadata available for validation and remarks. Do not confuse GCP's generic `currency_exchange_rate` with the HKD-per-USD business rate unless the source explicitly says it is that rate.

## Account Formulas

For each billing account:

- `gcp_total_cost`: sum of `unrounded_cost_usd` where `credit_type` is blank.
- `gcp_net_cost`: sum of all `unrounded_cost_usd`, including discounts and credits.
- `reseller_margin_usd`: sum of `unrounded_cost_usd` where normalized `credit_type = RESELLER_MARGIN`.
- `customer_charge_prediscount`: `gcp_net_cost - reseller_margin_usd`.
- `customer_charge`: `customer_charge_prediscount * (1 - special_discount_pct)`.
- `customer_charge_hkd`: `customer_charge * hkd_exchange_rate`.
- `gross_profit`: `customer_charge - gcp_net_cost`.
- `gross_profit_pct`: `gross_profit / customer_charge` when `customer_charge != 0`.
- `item_count`: raw cost row count for the account after excluding footer rows.

Normalize `credit_type` with trim and uppercase before comparing. Exclude footer rows whose `cost_type` is `Rounding error` or `Total`.

## Aggregated Result Output

The aggregated output should expose semantic rows for review and reconciliation.

Expected levels:

- one summary row,
- one row per category when category rules are available,
- one row per account/customer.

Expected fields:

- `row_type`: `summary`, `category`, or `account`
- `category`
- `account_id`
- `customer`
- `usage_before_reseller_margin_usd`
- `gcp_net_cost_usd`
- `discount_pct`
- `customer_charge_usd`
- `customer_charge_hkd`
- `gross_profit_usd`
- `gross_profit_pct`
- `item_count`

Reference workbooks can be used for shape and reconciliation intent, not for raw output field names.

## IBS Charge-Item Output

The IBS output should produce rows shaped like the `Bill Item` sheet in the IBS upload template. This file is fixed enough that the skill should babysit it closely rather than leave agents to infer formatting.

Expected fields visible in the reference:

- unlabeled customer-name column
- `bill_acct`
- `contct_id`
- `chrg_code`
- `chrg_amt`
- `start_bill`
- `end_bill`
- `remark1`
- `remark2`
- `remark3`
- `remark4`
- `bill_date`
- `ccc`
- `bill_methd`
- `uploaded`

Semantics:

- `chrg_amt` is the HKD customer charge.
- `chrg_code` defaults to `DY80` for this GCP IBS upload.
- `ccc` defaults to `CAM8`.
- `bill_methd` defaults to `O`.
- `uploaded` defaults to `N`.
- `start_bill` and `end_bill` use the billing month in numeric-looking `YYYYMMDD` form.
- `bill_date` uses the upload bill date in numeric-looking `YYYYMMDD` form.
- `remark1` commonly carries `GCP Usage Consumption`.
- `remark2` commonly carries the USD service charge.
- `remark3` can carry a discount or billing-account context.
- `remark4` can carry the HKD exchange-rate context.
- The last row should be a template-style total row:
  - first column blank,
  - `bill_acct = Total Rec`,
  - `contct_id = number of bill-item rows`,
  - `chrg_code = Total Amt`,
  - `chrg_amt = sum of rounded bill-item amounts`.

IBS-only customer fields are allowed to come from maintained mapping/defaults inside the implementation used by the run. Accounts without maintained mapping should be reported as skipped for IBS, not as a failure of the raw cost-table analysis.

## Stability Rules

- Do not hardcode generated table names, content hashes, or billing months.
- Do not use raw workbook labels as final semantic names.
- Keep formulas in an inspectable tabular transformation.
- Do not require example workbooks, PDFs, emails, or config CSVs as default user inputs for this skill.
- Validate totals against the raw cost table before treating outputs as done.
