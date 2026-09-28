# Bundle Gross Profit costing

Cost Amount and profit use the signed `stock_value_difference` from the exact
Sales Invoice or Delivery Note stock-ledger item. Zero values remain zero.
Average Cost is the historical reference estimate and does not determine profit.

- Stock-updating invoices use their own stock ledger.
- Other invoices use explicit invoice/delivery item links, including Delivery
  Note Item `si_detail` for deliveries made after invoicing. When both documents
  were created independently from a Sales Order, unlinked positive invoice rows
  are allocated through the same company, item, Sales Order and `so_detail`.
  Explicit links reserve stock first. Remaining invoice demand and deliveries
  are matched in posting order across all dates, so filtering the report does
  not allocate the same stock twice. This is a stated allocation rule rather
  than a new document link; no documents are edited. Matching only the order
  header or guessing return movements is not allowed.
- Partial invoices receive their stock-quantity share of a delivery item's cost.
  Multiple explicitly linked deliveries are allocated separately. Bundle
  components are scaled by the invoiced share of the delivered parent quantity.
- Cancelled movements are excluded. Stock returns produce negative costs.
  Credit notes without a matching return movement do not reverse an original
  delivery's cost; they are flagged for review.
- Conflicting links, excess quantities and missing movements remain visible as
  **Review required**. Known costs are retained, but incomplete row profits and
  the overall profit summary are withheld. Do not treat known cost as a complete
  COGS total until these rows are resolved.
- Custom BOM zero-sales rows are suppressed only when the exact movement and
  quantity are already represented in a parent. Tree totals are disabled to
  prevent counting both parent and child costs.

## Reconcile COGS

Enable **Reconcile COGS**, select company/dates, and optionally choose a leaf
expense account such as `Cost of Goods Sold - YT`. Without an account selection,
all accessible accounts of type **Cost of Goods Sold** are included. Clear other
report filters first; a filtered sales subset is not comparable to company COGS.

The reconciliation groups allocated invoice costs and GL debit minus credit by
voucher. It includes unmatched deliveries and adjustments such as Purchase
Receipt credits. It respects GL and Account read permissions. Opening GL entries
are excluded. Invoice dates and GL posting dates can differ, so an unmatched
voucher is a review item, not proof of an accounting error.

This report only reads records. It does not repair submitted document links or
change stock valuations/accounting entries automatically.

## Validation and deployment

Regression tests cover signed returns, true zero costs, exact stock detail
matching, partial invoices, multiple deliveries, duplicate links, bundle
quantity scaling, and voucher reconciliation. `test_stock_cost_queries` executes
the allocation SQL against SQLite fixtures. This supplements rather than
replaces validation on the deployed ERPNext/MariaDB site.

Run on a test site with the app installed:

```bash
bench --site TEST_SITE run-tests --app it --module it.it.report.bundle_gross_profit.test_stock_costs
bench --site TEST_SITE run-tests --app it --module it.it.report.bundle_gross_profit.test_stock_cost_queries
bench --site TEST_SITE run-tests --app it --module it.it.report.bundle_gross_profit.test_bundle_gross_profit
```

Deploy all changed/new files together. From the server bench directory after
updating the app checkout:

```bash
bench --site yt.connect4systems.com clear-cache
bench restart
```

Reload the browser and run a fresh report rather than opening a previously
prepared result. Check the zero-valued laptop/RAM movements in the comparison period,
return invoice `ACC-SINV-2026-00238`, and delivery quantity/link warnings before
accepting the reconciled totals. A fresh live export is required to determine
the corrected totals; the old spreadsheets lack exact stock detail links.
