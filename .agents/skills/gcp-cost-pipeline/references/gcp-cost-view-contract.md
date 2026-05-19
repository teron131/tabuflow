# GCP Cost Output Contract

Use this reference when a task needs the GCP cost-table field mapping,
formulas, or output shape.

The source input is one monthly GCP cost table, conceptually
`cost_table.xlsx`. The current example fixture is `examples/gcp/cost_table.csv`.

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

Use the raw names only at this boundary. Downstream logic should use semantic
fields.

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

Keep invoice metadata available for validation and remarks. Do not confuse GCP's
generic `currency_exchange_rate` with the HKD-per-USD business rate unless the
source explicitly says it is that rate.

## Account Formulas

For each billing account:

- `gcp_total_cost`: sum of `unrounded_cost_usd` where `credit_type` is blank.
- `gcp_net_cost`: sum of all `unrounded_cost_usd`, including discounts and
  credits.
- `reseller_margin_usd`: sum of `unrounded_cost_usd` where normalized
  `credit_type = RESELLER_MARGIN`.
- `customer_charge_prediscount`: `gcp_net_cost - reseller_margin_usd`.
- `customer_charge`: `customer_charge_prediscount * (1 - special_discount_pct)`.
- `customer_charge_hkd`: `customer_charge * hkd_exchange_rate`.
- `gross_profit`: `customer_charge - gcp_net_cost`.
- `gross_profit_pct`: `gross_profit / customer_charge` when
  `customer_charge != 0`.
- `item_count`: raw cost row count for the account after excluding footer rows.

Normalize `credit_type` with trim and uppercase before comparing. Exclude footer
rows whose `cost_type` is `Rounding error` or `Total`.

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

Reference examples:

- `examples/gcp/aggregated_cost_table.xlsx`
- `examples/gcp/aggregated_cost_table2.xlsx`

Use these workbooks for shape and reconciliation intent, not for raw output
field names.

## IBS Charge-Item Output

The IBS output should produce rows shaped like the `Bill Item` sheet in
`examples/gcp/IBS_ChargeItemUploadTemplate_Cloud_GCP_20260312.xls`.

Expected fields visible in the reference:

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
- `remark1` commonly carries `GCP Usage Consumption`.
- `remark2` commonly carries the USD service charge.
- `remark3` can carry a discount or billing-account context.
- `remark4` can carry the HKD exchange-rate context.

If `bill_acct`, `contct_id`, or other IBS-only customer fields cannot be derived
from the raw cost table or maintained mapping, report that mapping gap directly.

## Stability Rules

- Do not hardcode generated table names, content hashes, or billing months.
- Do not use raw workbook labels as final semantic names.
- Keep formulas in an inspectable tabular transformation.
- Do not require example workbooks, PDFs, emails, or config CSVs as default user
  inputs for this skill.
- Validate totals against the raw cost table before treating outputs as done.
