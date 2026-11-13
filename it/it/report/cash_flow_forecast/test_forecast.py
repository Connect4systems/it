# Copyright (c) 2026, Connect 4 Systems
# See license.txt

import unittest
from datetime import date
from decimal import Decimal

from it.it.report.cash_flow_forecast.forecast import build_rows, company_amount, get_bucket, get_months


def invoice(name="SI-1", **values):
	return {
		"name": name,
		"invoice_type": "Sales Invoice",
		"posting_date": "2026-06-01",
		"due_date": "2026-12-15",
		"party": "Test Customer",
		"outstanding_amount": 250,
		"party_account_currency": "EGP",
		"currency": "EGP",
		"conversion_rate": 1,
		**values,
	}


class TestForecast(unittest.TestCase):
	def rows(self, invoices, **options):
		return build_rows(invoices, "2026-11-01", "2026-12-31", "EGP", **options)

	def test_invoice_is_grouped_by_due_date_not_posting_date(self):
		rows = self.rows([invoice()])
		self.assertEqual(rows[0]["month_2026_11"], 0)
		self.assertEqual(rows[0]["month_2026_12"], 250)
		self.assertEqual(rows[1]["due_date"], "2026-12-15")
		self.assertEqual(rows[1]["invoice"], "SI-1")

	def test_partial_payment_uses_remaining_outstanding_only(self):
		rows = self.rows([invoice(outstanding_amount=75, grand_total=250)])
		self.assertEqual(rows[-1]["total"], 75)

	def test_supplier_payments_are_negative_and_details_are_not_double_counted(self):
		rows = self.rows(
			[
				invoice(),
				invoice("PI-1", invoice_type="Purchase Invoice", party="Supplier", outstanding_amount=100),
			]
		)
		self.assertEqual(rows[2]["total"], -100)
		self.assertEqual(rows[3]["total"], -100)
		self.assertEqual(rows[-1]["month_2026_12"], 150)
		self.assertEqual(rows[-1]["total"], 150)

	def test_zero_balances_and_dates_after_range_are_excluded(self):
		rows = self.rows([invoice(outstanding_amount=0), invoice("SI-2", due_date="2027-01-01")])
		self.assertEqual(len(rows), 3)
		self.assertEqual(rows[-1]["total"], 0)

	def test_earlier_and_missing_due_dates_remain_visible(self):
		rows = self.rows([invoice(due_date="2026-10-31"), invoice("SI-2", due_date=None)])
		self.assertEqual(rows[0]["earlier"], 250)
		self.assertEqual(rows[0]["undated"], 250)
		self.assertEqual(rows[0]["month_2026_11"], 0)
		self.assertEqual(rows[-1]["total"], 500)

	def test_earlier_invoices_can_be_excluded(self):
		rows = self.rows([invoice(due_date="2026-10-31")], include_earlier=False)
		self.assertNotIn("earlier", rows[0])
		self.assertEqual(rows[-1]["total"], 0)

	def test_exact_date_boundaries_with_partial_months(self):
		self.assertEqual(get_bucket("2026-11-15", "2026-11-15", "2026-12-10", True), "month_2026_11")
		self.assertEqual(get_bucket("2026-12-10", "2026-11-15", "2026-12-10", True), "month_2026_12")
		self.assertEqual(get_bucket("2026-11-14", "2026-11-15", "2026-12-10", True), "earlier")
		self.assertIsNone(get_bucket("2026-12-11", "2026-11-15", "2026-12-10", True))

	def test_foreign_account_outstanding_is_converted_at_invoice_rate(self):
		rows = self.rows([invoice(currency="USD", party_account_currency="USD", conversion_rate=50)])
		self.assertEqual(rows[-1]["total"], 12500)

	def test_company_currency_account_is_not_converted_twice(self):
		amount = company_amount(invoice(currency="USD", conversion_rate=50), "EGP")
		self.assertEqual(amount, Decimal(250))

	def test_invalid_exchange_rate_and_account_currency_are_rejected(self):
		for values in (
			{"party_account_currency": "USD", "currency": "USD", "conversion_rate": 0},
			{"party_account_currency": "USD", "currency": "EUR", "conversion_rate": 50},
			{"party_account_currency": None},
		):
			with self.subTest(values=values), self.assertRaises(ValueError):
				company_amount(invoice(**values), "EGP")

	def test_unallocated_credit_balances_reverse_cash_direction(self):
		rows = self.rows(
			[
				invoice(outstanding_amount=-50),
				invoice("PI-1", invoice_type="Purchase Invoice", outstanding_amount=-25),
			]
		)
		self.assertEqual(rows[0]["total"], -50)
		self.assertEqual(rows[2]["total"], 25)
		self.assertEqual(rows[-1]["total"], -25)

	def test_summary_matches_detail_totals(self):
		invoices = [invoice(), invoice("SI-2", due_date=None), invoice("SI-3", due_date="2026-10-01")]
		details = self.rows(invoices)
		summary = self.rows(invoices, show_details=False)
		self.assertEqual(len(summary), 3)
		self.assertEqual(summary[0], details[0])
		self.assertEqual(summary[-1], details[-1])

	def test_months_cross_year_and_include_empty_months(self):
		self.assertEqual(
			get_months("2026-12-15", "2027-02-01"),
			[
				date(2026, 12, 1),
				date(2027, 1, 1),
				date(2027, 2, 1),
			],
		)

	def test_invalid_date_ranges_are_rejected(self):
		for start, end in (("2026-12-01", "2026-11-01"), ("2026-01-01", "2036-01-01")):
			with self.subTest(start=start, end=end), self.assertRaises(ValueError):
				get_months(start, end)

	def test_decimal_totals_are_consistent(self):
		rows = self.rows([invoice("SI-1", outstanding_amount=0.1), invoice("SI-2", outstanding_amount=0.2)])
		self.assertEqual(rows[-1]["total"], 0.3)
