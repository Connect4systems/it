# Copyright (c) 2026, Connect 4 Systems
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from it.it.report.bundle_gross_profit import bundle_gross_profit


class TestBundleGrossProfit(FrappeTestCase):
	def test_split_serial_numbers_supports_legacy_formats(self):
		self.assertEqual(
			bundle_gross_profit._split_serial_numbers("SER-1\nSER-2, SER-3\r\nSER-4"),
			["SER-1", "SER-2", "SER-3", "SER-4"],
		)

	@patch.object(bundle_gross_profit, "_get_purchase_receipt_serial_rate")
	@patch.object(bundle_gross_profit, "_get_outgoing_serial_numbers")
	def test_serialized_cost_uses_each_purchase_receipt_rate(self, get_serial_numbers, get_serial_rate):
		get_serial_numbers.return_value = ["SER-1", "SER-2", "SER-3"]
		get_serial_rate.side_effect = [
			(100, "MAT-PRE-0001"),
			(120, "MAT-PRE-0002"),
			(110, "MAT-PRE-0003"),
		]

		average_cost, cost_amount, cost_basis = bundle_gross_profit._get_serialized_item_cost(
			frappe._dict(item_code="SERIAL-ITEM"),
			3,
			{"has_serial_no": {}, "outgoing_cutoffs": {}, "serial_rates": {}},
		)

		self.assertEqual(average_cost, 110)
		self.assertEqual(cost_amount, 330)
		self.assertIn("Serial Purchase Receipt", cost_basis)

	@patch.object(bundle_gross_profit, "_get_purchase_receipt_serial_rate")
	@patch.object(bundle_gross_profit, "_get_outgoing_serial_numbers")
	def test_missing_serial_purchase_cost_is_flagged(self, get_serial_numbers, get_serial_rate):
		get_serial_numbers.return_value = ["SER-1", "SER-MISSING"]
		get_serial_rate.side_effect = [(100, "MAT-PRE-0001"), None]

		average_cost, cost_amount, cost_basis = bundle_gross_profit._get_serialized_item_cost(
			frappe._dict(item_code="SERIAL-ITEM"),
			2,
			{"has_serial_no": {}, "outgoing_cutoffs": {}, "serial_rates": {}},
		)

		self.assertEqual(average_cost, 50)
		self.assertEqual(cost_amount, 100)
		self.assertIn("SER-MISSING", cost_basis)

	@patch.object(bundle_gross_profit, "_get_closest_valuation_rate")
	@patch.object(bundle_gross_profit, "_get_purchase_receipt_serial_rate")
	@patch.object(bundle_gross_profit, "_get_outgoing_serial_numbers")
	def test_missing_serial_purchase_cost_uses_closest_valuation(
		self, get_serial_numbers, get_serial_rate, get_valuation_rate
	):
		get_serial_numbers.return_value = ["SER-1", "SER-2"]
		get_serial_rate.side_effect = [(100, "MAT-PRE-0001"), None]
		get_valuation_rate.return_value = (115, "2026-07-10 12:00:00")

		average_cost, cost_amount, cost_basis = bundle_gross_profit._get_serialized_item_cost(
			frappe._dict(item_code="SERIAL-ITEM", posting_date="2026-07-11"),
			2,
			{"has_serial_no": {}, "outgoing_cutoffs": {}, "serial_rates": {}},
		)

		self.assertEqual(average_cost, 107.5)
		self.assertEqual(cost_amount, 215)
		self.assertIn("Closest Stock Ledger valuation", cost_basis)
		self.assertNotIn("missing:", cost_basis)

	@patch.object(bundle_gross_profit, "_get_outgoing_stock_ledger_rows")
	def test_fifo_cost_uses_outgoing_stock_value(self, get_ledger_rows):
		get_ledger_rows.return_value = [
			frappe._dict(actual_qty=-2, stock_value_difference=-200, outgoing_rate=0),
			frappe._dict(actual_qty=-3, stock_value_difference=-360, outgoing_rate=0),
		]

		average_cost = bundle_gross_profit._get_fifo_stock_ledger_average(frappe._dict(item_code="FIFO-ITEM"))

		self.assertEqual(average_cost, 112)

	@patch.object(bundle_gross_profit, "_get_outgoing_stock_ledger_rows")
	def test_zero_value_fifo_issue_is_a_valid_zero_cost(self, get_ledger_rows):
		get_ledger_rows.return_value = [
			frappe._dict(
				actual_qty=-1,
				stock_value_difference=0,
				outgoing_rate=0,
				valuation_rate=0,
			)
		]

		average_cost = bundle_gross_profit._get_fifo_stock_ledger_average(
			frappe._dict(item_code="FREE-STOCK-ITEM")
		)

		self.assertEqual(average_cost, 0)

	@patch.object(bundle_gross_profit, "_has_column", return_value=True)
	@patch.object(frappe.db, "sql")
	def test_purchase_receipt_item_rate_uses_base_amount_per_stock_unit(self, db_sql, _has_column):
		db_sql.return_value = [
			frappe._dict(
				base_net_amount=3492118.06,
				stock_qty=50,
				base_net_rate=69842.36,
				conversion_factor=1,
				base_rate=69842.36,
			)
		]

		rate = bundle_gross_profit._get_purchase_receipt_item_rate(
			"MAT-PRE-2026-00243", "PRI-ROW-1", "21S7S3W90A"
		)

		self.assertAlmostEqual(rate, 69842.3612)

	@patch.object(bundle_gross_profit, "_get_purchase_receipt_ledger_rate")
	@patch.object(bundle_gross_profit, "_get_purchase_receipt_item_rate")
	@patch.object(bundle_gross_profit, "_has_column", return_value=True)
	@patch.object(frappe.db, "sql")
	def test_serial_bundle_prefers_purchase_receipt_item_rate(
		self, db_sql, _has_column, get_receipt_item_rate, get_ledger_rate
	):
		db_sql.return_value = [
			frappe._dict(
				purchase_receipt="MAT-PRE-2026-00243",
				purchase_receipt_item="PRI-ROW-1",
				incoming_rate=116403.94,
			)
		]
		get_receipt_item_rate.return_value = 69842.36

		rate, purchase_receipt = bundle_gross_profit._get_serial_bundle_purchase_receipt_rate(
			"21S7S3W90A", "SER-1"
		)

		self.assertEqual(rate, 69842.36)
		self.assertEqual(purchase_receipt, "MAT-PRE-2026-00243")
		get_ledger_rate.assert_not_called()
