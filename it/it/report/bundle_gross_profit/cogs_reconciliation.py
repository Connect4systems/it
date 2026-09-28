"""Voucher reconciliation; invoice dates and GL posting dates intentionally remain distinct."""

from collections import defaultdict

import frappe
from frappe import _
from frappe.utils import flt


def execute_reconciliation(filters, invoice_rows):
	if not frappe.has_permission("GL Entry", "read"):
		frappe.throw(
			_("General Ledger read permission is required to reconcile COGS"), frappe.PermissionError
		)
	account_filters = {"company": filters.company, "is_group": 0, "root_type": "Expense"}
	if filters.get("cogs_account"):
		account_filters["name"] = filters.cogs_account
	else:
		account_filters["account_type"] = "Cost of Goods Sold"
	accounts = frappe.get_list("Account", filters=account_filters, pluck="name", limit_page_length=0)
	if not accounts:
		frappe.throw(_("Select an accessible COGS account for this company"))
	# get_list preserves document/user permissions; do not expose unrestricted GL via raw SQL.
	ledger = frappe.get_list(
		"GL Entry",
		filters={
			"company": filters.company,
			"account": ["in", accounts],
			"is_cancelled": 0,
			"posting_date": ["between", [filters.from_date, filters.to_date]],
			"is_opening": "No",
		},
		fields=["voucher_type", "voucher_no", "posting_date", "debit", "credit"],
		limit_page_length=0,
	)
	data = reconcile(invoice_rows, ledger)
	columns = [
		{"label": _("Voucher Type"), "fieldname": "voucher_type", "fieldtype": "Data", "width": 130},
		{
			"label": _("Voucher"),
			"fieldname": "voucher_no",
			"fieldtype": "Dynamic Link",
			"options": "voucher_type",
			"width": 190,
		},
		{"label": _("GL Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 110},
		{"label": _("Sales Invoices"), "fieldname": "sales_invoices", "fieldtype": "Data", "width": 220},
		{"label": _("Invoice Dates"), "fieldname": "invoice_dates", "fieldtype": "Data", "width": 180},
		{
			"label": _("Allocated Invoice Cost"),
			"fieldname": "invoice_cost",
			"fieldtype": "Currency",
			"width": 160,
		},
		{"label": _("GL Net COGS"), "fieldname": "gl_cost", "fieldtype": "Currency", "width": 140},
		{
			"label": _("Invoice Cost minus GL"),
			"fieldname": "difference",
			"fieldtype": "Currency",
			"width": 170,
		},
		{"label": _("Reconciliation Status"), "fieldname": "status", "fieldtype": "Data", "width": 350},
	]
	incomplete = sum(bool(r.get("cost_incomplete")) for r in invoice_rows if not r.get("indent"))
	summary = [
		{
			"label": _("Known Invoice Cost"),
			"value": sum(r["invoice_cost"] for r in data),
			"datatype": "Currency",
			"indicator": "Blue",
		},
		{
			"label": _("GL Net COGS"),
			"value": sum(r["gl_cost"] for r in data),
			"datatype": "Currency",
			"indicator": "Orange",
		},
		{
			"label": _("Invoice Cost minus GL"),
			"value": sum(r["difference"] for r in data),
			"datatype": "Currency",
			"indicator": "Orange",
		},
		{
			"label": _("Rows requiring cost review"),
			"value": incomplete,
			"datatype": "Int",
			"indicator": "Orange" if incomplete else "Green",
		},
	]
	message = _(
		"Invoice costs use invoice dates; GL uses posting dates and the selected COGS account "
		"(or all accessible accounts of type Cost of Goods Sold). Opening entries are excluded. "
		"Unmatched deliveries can be uninvoiced, outside the invoice period, or missing a link. "
		"Other vouchers are shown as COGS adjustments. Unlinked invoices created from Sales Orders "
		"use available delivery quantities on the exact order item in posting order, after explicit links. "
		"Review incomplete costs before interpreting profit."
	)
	return columns, data, message, None, summary, True


def reconcile(invoice_rows, ledger):
	groups = defaultdict(
		lambda: {
			"invoice_cost": 0,
			"gl_cost": 0,
			"invoices": set(),
			"dates": set(),
			"posting_date": None,
			"has_gl": False,
			"has_invoice": False,
			"incomplete": False,
		}
	)
	for row in invoice_rows:
		if row.get("indent"):
			continue
		allocations = row.get("stock_allocations") or []
		if not allocations and row.get("cost_incomplete"):
			allocations = [{"voucher_type": "Sales Invoice", "voucher_no": row["sales_invoice"], "cost": 0}]
		for allocation in allocations:
			group = groups[(allocation["voucher_type"], allocation["voucher_no"])]
			group["invoice_cost"] += flt(allocation["cost"])
			group["invoices"].add(row["sales_invoice"])
			group["dates"].add(str(row["posting_date"]))
			group["has_invoice"] = True
			group["incomplete"] = group["incomplete"] or row.get("cost_incomplete", False)
	for entry in ledger:
		group = groups[(entry["voucher_type"], entry["voucher_no"])]
		group["gl_cost"] += flt(entry["debit"]) - flt(entry["credit"])
		group["posting_date"] = entry["posting_date"]
		group["has_gl"] = True
	result = []
	for (voucher_type, voucher_no), group in sorted(groups.items()):
		difference = group["invoice_cost"] - group["gl_cost"]
		if group["incomplete"]:
			status = _("Incomplete invoice cost; review report cost status")
		elif not group["has_invoice"]:
			status = (
				_("No linked invoice cost in period; check billing, dates and links")
				if voucher_type in ("Delivery Note", "Sales Invoice")
				else _("Other COGS adjustment")
			)
		elif not group["has_gl"]:
			status = _("No GL COGS in period/account; check dates and account allocation")
		elif abs(difference) > 0.01:
			status = _("Difference; check partial billing, stock valuation and account allocation")
		else:
			status = _("Matched")
		result.append(
			{
				"voucher_type": voucher_type,
				"voucher_no": voucher_no,
				"posting_date": group["posting_date"],
				"sales_invoices": ", ".join(sorted(group["invoices"])),
				"invoice_dates": ", ".join(sorted(group["dates"])),
				"invoice_cost": group["invoice_cost"],
				"gl_cost": group["gl_cost"],
				"difference": difference,
				"status": status,
			}
		)
	return result
