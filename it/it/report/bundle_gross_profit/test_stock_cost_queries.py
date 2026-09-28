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
			CREATE TABLE `tabSales Invoice` (name TEXT, company TEXT, docstatus INT, update_stock INT,
				posting_date TEXT DEFAULT '2026-09-01', posting_time TEXT DEFAULT '10:00:00');
			CREATE TABLE `tabSales Invoice Item` (name TEXT, parent TEXT, item_code TEXT,
				stock_qty REAL, delivery_note TEXT, dn_detail TEXT, sales_order TEXT DEFAULT 'SO-1',
				so_detail TEXT DEFAULT 'SOI-1', idx INT DEFAULT 1);
			CREATE TABLE `tabDelivery Note` (name TEXT, company TEXT, docstatus INT,
				posting_date TEXT, posting_time TEXT);
			CREATE TABLE `tabDelivery Note Item` (name TEXT, parent TEXT, item_code TEXT, qty REAL,
				stock_qty REAL, warehouse TEXT, si_detail TEXT, against_sales_invoice TEXT, idx INT,
				against_sales_order TEXT DEFAULT 'SO-1', so_detail TEXT DEFAULT 'SOI-1');
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
		self.db.execute(
			"INSERT INTO `tabSales Invoice` (name, company, docstatus, update_stock) VALUES (?, 'YT', ?, 0)",
			(name, status),
		)
		self.db.execute(
			"INSERT INTO `tabSales Invoice Item` (name, parent, item_code, stock_qty, delivery_note, dn_detail) VALUES (?, ?, 'ITEM', ?, ?, ?)",
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
			"INSERT INTO `tabDelivery Note Item` (name, parent, item_code, qty, stock_qty, warehouse, si_detail, against_sales_invoice, idx) VALUES (?, ?, 'ITEM', ?, ?, 'WH', ?, ?, 1)",
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

	def order_invoice(self, **kwargs):
		return invoice(delivery_note=None, dn_detail=None, sales_order="SO-1", so_detail="SOI-1", **kwargs)

	def test_order_created_documents_match_without_direct_invoice_link(self):
		self.add_invoice(delivery=None, dn_detail=None)
		self.add_delivery()
		sources, issue = invoice_sources(self.order_invoice())
		self.assertIsNone(issue)
		self.assertEqual(sources[0].delivery_note, "DN-1")
		self.assertEqual(ledger_cost(sources[0])[1], 200)
		self.assertIn("Sales Order", sources[0].allocation_basis)

	def test_order_allocation_is_independent_of_which_invoice_is_requested_first(self):
		self.add_invoice(qty=3, delivery=None, dn_detail=None)
		self.add_invoice(name="SI-2", detail="SII-2", qty=4, delivery=None, dn_detail=None)
		self.add_delivery(qty=5)
		self.add_delivery(name="DN-2", detail="DNI-2", qty=2)
		second = self.order_invoice(sales_invoice="SI-2", sales_invoice_item="SII-2", stock_qty=4)
		sources, issue = invoice_sources(second)
		self.assertIsNone(issue)
		self.assertEqual([(s.delivery_note, s.stock_qty) for s in sources], [("DN-1", 2), ("DN-2", 2)])
		first_sources, issue = invoice_sources(self.order_invoice(stock_qty=3))
		self.assertEqual(sum(ledger_cost(s)[1] for s in first_sources + sources), 700)
		self.assertEqual(
			[(s.delivery_note, s.stock_qty) for s in invoice_sources(second)[0]], [("DN-1", 2), ("DN-2", 2)]
		)

	def test_order_allocation_reserves_explicit_invoices_first(self):
		self.add_invoice(qty=2, delivery=None, dn_detail=None)
		self.add_invoice(name="SI-2", detail="SII-2", qty=3)
		self.add_delivery(qty=5)
		sources, issue = invoice_sources(self.order_invoice())
		self.assertIsNone(issue)
		self.assertEqual(sources[0].stock_qty, 2)

	def test_order_allocation_never_consumes_other_order_row_or_reverse_link(self):
		self.add_invoice(delivery=None, dn_detail=None)
		self.add_delivery(si_detail="OTHER-SII", invoice_no="OTHER-SI")
		self.add_delivery(name="DN-2", detail="DNI-2")
		self.db.execute("UPDATE `tabDelivery Note Item` SET so_detail='SOI-OTHER' WHERE name='DNI-2'")
		sources, issue = invoice_sources(self.order_invoice())
		self.assertEqual(sources, [])
		self.assertIn("insufficient", issue)

	def test_order_matching_does_not_infer_a_return_for_credit_only_invoice(self):
		self.add_invoice(qty=-2, delivery=None, dn_detail=None)
		self.add_delivery(qty=-2)
		self.assertEqual(invoice_sources(self.order_invoice(stock_qty=-2))[0], [])
