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
