"""Execute the allocation queries against a small SQL fixture, without a site database."""

import re
import sqlite3
from unittest import TestCase
from unittest.mock import patch

import frappe

from .stock_costs import invoice_sources, ledger_cost
from .test_stock_costs import invoice


class TestStockCostQueries(TestCase):
	def setUp(self):
		self.db = sqlite3.connect(":memory:")
		self.db.row_factory = sqlite3.Row
		self.db.executescript("""
			CREATE TABLE `tabSales Invoice` (name TEXT, company TEXT, docstatus INT, update_stock INT);
			CREATE TABLE `tabSales Invoice Item` (name TEXT, parent TEXT, item_code TEXT,
				stock_qty REAL, delivery_note TEXT, dn_detail TEXT);
			CREATE TABLE `tabDelivery Note` (name TEXT, company TEXT, docstatus INT,
				posting_date TEXT, posting_time TEXT);
			CREATE TABLE `tabDelivery Note Item` (name TEXT, parent TEXT, item_code TEXT, qty REAL,
				stock_qty REAL, warehouse TEXT, si_detail TEXT, against_sales_invoice TEXT, idx INT);
			CREATE TABLE `tabStock Ledger Entry` (company TEXT, voucher_type TEXT, voucher_no TEXT,
				voucher_detail_no TEXT, item_code TEXT, is_cancelled INT, actual_qty REAL,
				stock_value_difference REAL, posting_date TEXT, posting_time TEXT, creation TEXT);
		""")
		self.patch = patch.object(frappe.db, "sql", side_effect=self.sql)
		self.patch.start()

	def tearDown(self):
		self.patch.stop()
		self.db.close()

	def sql(self, query, params, as_dict=False):
		query = re.sub(r"%\((\w+)\)s", r":\1", query)
		return [frappe._dict(dict(row)) for row in self.db.execute(query, params)]

	def add_invoice(self, name="SI-1", detail="SII-1", qty=2, delivery="DN-1", dn_detail="DNI-1", status=1):
		self.db.execute("INSERT INTO `tabSales Invoice` VALUES (?, 'YT', ?, 0)", (name, status))
		self.db.execute(
			"INSERT INTO `tabSales Invoice Item` VALUES (?, ?, 'ITEM', ?, ?, ?)",
			(detail, name, qty, delivery, dn_detail),
		)

	def add_delivery(
		self, name="DN-1", detail="DNI-1", qty=2, si_detail=None, invoice_no=None, status=1, company="YT"
	):
		self.db.execute(
			"INSERT INTO `tabDelivery Note` VALUES (?, ?, ?, '2026-09-01', '10:00:00')",
			(name, company, status),
		)
		self.db.execute(
			"INSERT INTO `tabDelivery Note Item` VALUES (?, ?, 'ITEM', ?, ?, 'WH', ?, ?, 1)",
			(detail, name, qty, qty, si_detail, invoice_no),
		)
		self.db.execute(
			"INSERT INTO `tabStock Ledger Entry` VALUES (?, 'Delivery Note', ?, ?, 'ITEM', 0, ?, ?, '2026-09-01', '10:00:00', '2026-09-01')",
			(company, name, detail, -qty, -qty * 100),
		)

	def test_real_queries_allocate_two_partial_invoices_without_duplicate_stock_value(self):
		self.add_delivery(qty=5)
		self.add_invoice(qty=2)
		self.add_invoice(name="SI-2", detail="SII-2", qty=3)
		cost = 0
		for row in [invoice(), invoice(sales_invoice="SI-2", sales_invoice_item="SII-2", stock_qty=3)]:
			sources, issue = invoice_sources(row)
			self.assertIsNone(issue)
			cost += ledger_cost(sources[0])[1]
		self.assertEqual(cost, 500)

	def test_cancelled_and_other_company_deliveries_do_not_match(self):
		self.add_invoice()
		for status, company in [(2, "YT"), (1, "Other")]:
			self.db.execute("DELETE FROM `tabDelivery Note`")
			self.db.execute("DELETE FROM `tabDelivery Note Item`")
			self.add_delivery(status=status, company=company)
			self.assertEqual(invoice_sources(invoice())[0], [])

	def test_real_queries_reject_duplicate_demand_but_ignore_cancelled_invoice(self):
		self.add_delivery(qty=2)
		self.add_invoice()
		self.add_invoice(name="SI-2", detail="SII-2", status=2)
		self.assertIsNone(invoice_sources(invoice())[1])
		self.db.execute("UPDATE `tabSales Invoice` SET docstatus = 1 WHERE name = 'SI-2'")
		self.assertEqual(invoice_sources(invoice())[0], [])

	def test_invoice_before_two_deliveries_uses_exact_reverse_links(self):
		self.add_invoice(qty=5, delivery=None, dn_detail=None)
		self.add_delivery(qty=2, si_detail="SII-1", invoice_no="SI-1")
		self.add_delivery(name="DN-2", detail="DNI-2", qty=3, si_detail="SII-1", invoice_no="SI-1")
		sources, issue = invoice_sources(invoice(stock_qty=5, delivery_note=None, dn_detail=None))
		self.assertIsNone(issue)
		self.assertEqual(sum(ledger_cost(source)[1] for source in sources), 500)

	def test_credit_note_does_not_reverse_original_delivery(self):
		self.add_invoice(qty=-1)
		self.add_delivery(qty=2)
		self.assertEqual(invoice_sources(invoice(stock_qty=-1))[0], [])
		self.add_delivery(name="DN-RETURN", detail="DNI-RETURN", qty=-1, si_detail="SII-1", invoice_no="SI-1")
		sources, issue = invoice_sources(invoice(stock_qty=-1))
		self.assertIsNone(issue)
		self.assertEqual(ledger_cost(sources[0])[1], -100)

	def test_cancelled_stock_entries_are_not_costed(self):
		self.add_delivery()
		self.db.execute("UPDATE `tabStock Ledger Entry` SET is_cancelled = 1")
		self.assertIsNone(ledger_cost(invoice())[1])
