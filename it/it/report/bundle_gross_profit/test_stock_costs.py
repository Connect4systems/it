from unittest import TestCase
from unittest.mock import patch

import frappe

from . import bundle_gross_profit as report
from . import cogs_reconciliation, stock_costs
from .cogs_reconciliation import reconcile


def invoice(**kwargs):
	row = frappe._dict(
		company="YT",
		sales_invoice="SI-1",
		sales_invoice_item="SII-1",
		item_code="ITEM",
		item_name="Item",
		qty=2,
		stock_qty=2,
		posting_date="2026-09-01",
		base_net_amount=500,
		customer="Customer",
		sales_partner=None,
		update_stock=0,
		delivery_note="DN-1",
		dn_detail="DNI-1",
	)
	row.update(kwargs)
	return row


class TestStockCosts(TestCase):
	@patch.object(frappe.db, "sql")
	def test_zero_stock_value_does_not_fall_back_to_valuation(self, sql):
		sql.return_value = [frappe._dict(actual_qty=-2, stock_value_difference=0, valuation_rate=47163.16)]
		rate, amount, _ = stock_costs.ledger_cost(invoice())
		self.assertEqual((rate, amount), (0, 0))
		query, params = sql.call_args.args
		self.assertIn("voucher_detail_no = %(detail)s", query)
		self.assertIn("company = %(company)s", query)
		self.assertEqual(params["detail"], "DNI-1")

	@patch.object(frappe.db, "sql")
	def test_return_cost_is_negative(self, sql):
		sql.return_value = [frappe._dict(actual_qty=2, stock_value_difference=200)]
		rate, amount, _ = stock_costs.ledger_cost(invoice(stock_qty=-2, qty=-2))
		self.assertEqual((rate, amount), (100, -200))

	@patch.object(frappe.db, "sql")
	def test_return_cannot_reuse_original_outgoing_cost(self, sql):
		sql.return_value = [frappe._dict(actual_qty=-2, stock_value_difference=-200)]
		_rate, amount, status = stock_costs.ledger_cost(invoice(stock_qty=-2, qty=-2))
		self.assertIsNone(amount)
		self.assertIn("direction", status)

	@patch.object(frappe.db, "sql")
	def test_partial_invoice_allocates_only_its_quantity(self, sql):
		sql.return_value = [frappe._dict(actual_qty=-10, stock_value_difference=-1200)]
		self.assertEqual(stock_costs.ledger_cost(invoice())[:2], (120, 240))

	@patch.object(frappe.db, "sql")
	def test_internal_transfer_uses_net_value(self, sql):
		sql.return_value = [
			frappe._dict(actual_qty=-2, stock_value_difference=-200),
			frappe._dict(actual_qty=2, stock_value_difference=200),
		]
		self.assertEqual(stock_costs.ledger_cost(invoice())[:2], (0, 0))

	@patch.object(frappe.db, "sql")
	def test_missing_exact_detail_does_not_query_entire_voucher(self, sql):
		sql.return_value = []
		self.assertIsNone(stock_costs.ledger_cost(invoice())[1])
		sql.assert_called_once()

	@patch.object(frappe.db, "sql")
	def test_overallocated_movement_is_flagged(self, sql):
		sql.return_value = [frappe._dict(actual_qty=-41, stock_value_difference=-64736.95)]
		self.assertIsNone(stock_costs.ledger_cost(invoice(stock_qty=50))[1])

	@patch.object(frappe.db, "sql")
	def test_packed_item_uses_parent_detail(self, sql):
		sql.return_value = [frappe._dict(actual_qty=-2, stock_value_difference=-200)]
		row = frappe._dict(
			company="YT",
			parenttype="Delivery Note",
			parent="DN-1",
			name="PACK-1",
			parent_detail_docname="DNI-1",
			item_code="PART",
			qty=1,
		)
		self.assertEqual(stock_costs.ledger_cost(row)[:2], (100, 100))
		self.assertEqual(sql.call_args.args[1]["detail"], "DNI-1")

	def test_stock_updating_invoice_uses_own_movement(self):
		sources, issue = stock_costs.invoice_sources(invoice(update_stock=1))
		self.assertIsNone(issue)
		self.assertIsNone(sources[0].delivery_note)
		self.assertEqual(sources[0].allocation_ratio, 1)

	@patch.object(frappe.db, "sql")
	def test_sales_order_is_never_used_to_guess_delivery(self, sql):
		sql.return_value = []
		sources, issue = stock_costs.invoice_sources(
			invoice(delivery_note=None, dn_detail=None, sales_order="SO-1")
		)
		self.assertEqual(sources, [])
		self.assertIsNotNone(issue)
		query = sql.call_args.args[0]
		self.assertNotIn("sales_order", query)
		self.assertIn("dni.stock_qty * %(qty)s > 0", query)

	@patch.object(frappe.db, "sql")
	def test_multiple_explicit_deliveries_allocate_once(self, sql):
		sql.side_effect = [
			[
				frappe._dict(
					name="DNI-1", parent="DN-1", stock_qty=3, qty=3, warehouse="WH", si_detail="SII-1"
				),
				frappe._dict(
					name="DNI-2", parent="DN-2", stock_qty=2, qty=2, warehouse="WH", si_detail="SII-1"
				),
			],
			[frappe._dict(qty=0)],
			[frappe._dict(qty=0)],
		]
		sources, issue = stock_costs.invoice_sources(
			invoice(stock_qty=5, qty=5, delivery_note=None, dn_detail=None)
		)
		self.assertIsNone(issue)
		self.assertEqual([r.stock_qty for r in sources], [3, 2])
		self.assertEqual([r.delivery_note for r in sources], ["DN-1", "DN-2"])

	@patch.object(frappe.db, "sql")
	def test_duplicate_invoice_links_use_all_time_demand(self, sql):
		sql.side_effect = [
			[frappe._dict(name="DNI-1", parent="DN-1", qty=50, stock_qty=50)],
			[frappe._dict(qty=80)],
		]
		sources, issue = stock_costs.invoice_sources(invoice(stock_qty=30))
		self.assertEqual(sources, [])
		self.assertIn("exceed", issue)
		self.assertNotIn("posting_date", sql.call_args.args[0])

	def test_stock_quantity_zero_is_not_replaced_by_sales_quantity(self):
		self.assertEqual(stock_costs.stock_qty(invoice(stock_qty=0, qty=2)), 0)

	@patch.object(report, "_get_item_cost", return_value=(500, 1000, "Purchase estimate"))
	@patch.object(report, "ledger_cost", return_value=(0, 0, "Actual Stock Ledger value"))
	@patch.object(report, "_get_components_for_invoice_item", return_value=[])
	@patch.object(report, "invoice_sources")
	@patch.object(report, "_is_zero_value_sales_order_bom_component", return_value=False)
	@patch.object(report, "_get_sales_invoice_items")
	@patch.object(report, "_is_non_stock_service", return_value=False)
	def test_profit_uses_actual_cost_not_reference_average(
		self, non_stock, items, skip, sources, components, cost, average
	):
		item = invoice()
		items.return_value = [item]
		sources.return_value = ([frappe._dict(item, allocation_ratio=1)], None)
		rows = report.get_data(frappe._dict())
		self.assertEqual(rows[0]["average_cost"], 500)
		self.assertEqual(rows[0]["actual_cost"], 0)
		self.assertEqual(rows[0]["cost_amount"], 0)
		self.assertEqual(rows[0]["gross_profit"], 500)

	@patch.object(report, "_get_item_cost", return_value=(100, 200, "Reference"))
	@patch.object(report, "ledger_cost", return_value=(100, 200, "Actual Stock Ledger value"))
	@patch.object(report, "_get_components_for_invoice_item")
	@patch.object(report, "invoice_sources")
	@patch.object(report, "_is_zero_value_sales_order_bom_component", return_value=False)
	@patch.object(report, "_get_sales_invoice_items")
	@patch.object(report, "_is_non_stock_service", return_value=False)
	def test_bundle_partial_invoice_scales_components(
		self, non_stock, items, skip, sources, components, cost, average
	):
		item = invoice()
		items.return_value = [item]
		sources.return_value = ([frappe._dict(item, allocation_ratio=0.2)], None)
		components.return_value = [
			frappe._dict(
				parenttype="Delivery Note",
				parent="DN-1",
				name="PACK",
				parent_detail_docname="DNI-1",
				item_code="PART",
				qty=10,
			)
		]
		rows = report.get_data(frappe._dict())
		self.assertEqual(cost.call_args.args[0].stock_qty, 2)
		self.assertEqual(rows[1]["qty"], 2)
		self.assertEqual(rows[0]["cost_amount"], 200)
		self.assertEqual(report.get_report_summary(rows)[1]["value"], 200)

	def test_incomplete_cost_suppresses_summary_profit(self):
		summary = report.get_report_summary(
			[{"sales_amount": 500, "cost_amount": 100, "cost_incomplete": True}]
		)
		self.assertEqual(summary[2]["value"], "Incomplete costs")
		self.assertEqual(summary[3]["datatype"], "Data")

	def test_zero_value_component_is_only_hidden_when_exact_cost_is_in_parent(self):
		allocation = {
			"voucher_type": "Delivery Note",
			"voucher_no": "DN-1",
			"detail": "DNI-1",
			"item_code": "PART",
			"qty": 2,
			"cost": 200,
		}
		parent = {"sales_invoice": "SI-1", "stock_allocations": [{**allocation, "component": True}]}
		child_invoice = {"sales_invoice": "SI-1", "_bom_component": True, "stock_allocations": [allocation]}
		self.assertEqual(len(report._remove_represented_component_rows([parent, child_invoice])), 1)
		child_invoice["_bom_component"] = True
		self.assertEqual(len(report._remove_represented_component_rows([child_invoice])), 1)

	@patch.object(frappe.db, "sql")
	def test_conflicting_forward_and_reverse_links_are_flagged(self, sql):
		sql.side_effect = [
			[frappe._dict(name="DNI-1", parent="DN-1", qty=50, stock_qty=50)],
			[frappe._dict(qty=20, conflicting_links=1)],
		]
		sources, issue = stock_costs.invoice_sources(invoice())
		self.assertEqual(sources, [])
		self.assertIn("conflict", issue)

	@patch.object(frappe.db, "sql")
	def test_partial_delivery_leaves_remaining_cost_unresolved(self, sql):
		sql.side_effect = [
			[frappe._dict(name="DNI-1", parent="DN-1", qty=1, stock_qty=1, warehouse="WH")],
			[frappe._dict(qty=0)],
		]
		sources, issue = stock_costs.invoice_sources(invoice(delivery_note=None, dn_detail=None))
		self.assertEqual(sources[0].stock_qty, 1)
		self.assertIn("Partially delivered", issue)


class TestReconciliation(TestCase):
	@patch.object(frappe, "has_permission", return_value=True)
	@patch.object(frappe, "get_list")
	def test_gl_query_uses_company_dates_account_and_permissions(self, get_list, permission):
		get_list.side_effect = [["COGS - YT"], []]
		filters = frappe._dict(
			company="YT", from_date="2026-01-01", to_date="2026-09-30", cogs_account="COGS - YT"
		)
		cogs_reconciliation.execute_reconciliation(filters, [])
		permission.assert_called_once_with("GL Entry", "read")
		account_filters = get_list.call_args_list[0].kwargs["filters"]
		self.assertEqual(account_filters["company"], "YT")
		self.assertEqual(account_filters["name"], "COGS - YT")
		gl_filters = get_list.call_args_list[1].kwargs["filters"]
		self.assertEqual(gl_filters["posting_date"], ["between", ["2026-01-01", "2026-09-30"]])
		self.assertEqual(gl_filters["account"], ["in", ["COGS - YT"]])
		self.assertEqual(gl_filters["is_cancelled"], 0)
		self.assertEqual(gl_filters["is_opening"], "No")

	def test_reconciliation_preserves_adjustments_and_excludes_child_costs(self):
		rows = [
			{
				"sales_invoice": "SI-1",
				"posting_date": "2026-09-01",
				"stock_allocations": [{"voucher_type": "Delivery Note", "voucher_no": "DN-1", "cost": 100}],
			},
			{"indent": 1, "cost_amount": 100},
		]
		ledger = [
			dict(
				voucher_type="Delivery Note",
				voucher_no="DN-1",
				posting_date="2026-09-01",
				debit=100,
				credit=0,
			),
			dict(
				voucher_type="Purchase Receipt",
				voucher_no="PR-1",
				posting_date="2026-09-01",
				debit=0,
				credit=87.72,
			),
			dict(
				voucher_type="Delivery Note", voucher_no="DN-2", posting_date="2026-09-01", debit=50, credit=0
			),
		]
		result = reconcile(rows, ledger)
		self.assertEqual(sum(r["invoice_cost"] for r in result), 100)
		self.assertAlmostEqual(sum(r["difference"] for r in result), 37.72)
		self.assertEqual(next(r for r in result if r["voucher_no"] == "DN-1")["status"], "Matched")
		self.assertEqual(
			next(r for r in result if r["voucher_no"] == "PR-1")["status"], "Other COGS adjustment"
		)

	def test_missing_cost_is_never_labeled_matched(self):
		rows = [{"sales_invoice": "SI-1", "posting_date": "2026-09-01", "cost_incomplete": True}]
		result = reconcile(rows, [])
		self.assertIn("Incomplete", result[0]["status"])
