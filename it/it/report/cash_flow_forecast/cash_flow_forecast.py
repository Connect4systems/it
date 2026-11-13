# Copyright (c) 2026, Connect 4 Systems
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint, getdate, nowdate

from it.it.report.cash_flow_forecast.forecast import build_rows, get_months, month_key


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if not filters.company or not filters.from_date or not filters.to_date:
		frappe.throw(_("Company, From Date and To Date are required"))
	try:
		months = get_months(filters.from_date, filters.to_date)
	except ValueError as error:
		frappe.throw(_(str(error)))
	company = frappe.get_doc("Company", filters.company)
	company.check_permission("read")
	for doctype in ("Sales Invoice", "Purchase Invoice"):
		frappe.has_permission(doctype, "read", throw=True)
	include_earlier = cint(filters.get("include_earlier", 1))
	show_details = cint(filters.get("show_details", 1))
	invoices = get_invoices(filters.company)
	try:
		data = build_rows(
			invoices,
			filters.from_date,
			filters.to_date,
			company.default_currency,
			include_earlier,
			show_details,
		)
	except ValueError as error:
		frappe.throw(_(str(error)))
	for row in data:
		if not row.get("indent"):
			row["flow"] = _(row["flow"])
	message = _(
		"Current outstanding balances as of {0}; this is not a historical snapshot. "
		"Amounts use invoice due dates and original invoice exchange rates. "
		"Payment schedules are not split into instalments. "
		"Undated amounts and any included earlier amounts contribute to Total, not to a forecast month. "
		"Negative amounts represent payments or customer refunds. "
		"Only invoices you have permission to read are included."
	).format(nowdate())
	return get_columns(months, filters.from_date, include_earlier, show_details), data, message


def get_invoices(company):
	rows = []
	for doctype, party in (("Sales Invoice", "customer"), ("Purchase Invoice", "supplier")):
		# get_list applies document/user permissions; get_all or raw SQL would bypass them.
		invoices = frappe.get_list(
			doctype,
			filters={
				"company": company,
				"docstatus": 1,
				"posting_date": ["<=", getdate(nowdate())],
				"outstanding_amount": ["!=", 0],
			},
			fields=[
				"name",
				"posting_date",
				"due_date",
				party,
				"outstanding_amount",
				"party_account_currency",
				"currency",
				"conversion_rate",
			],
			order_by="due_date asc, name asc",
			limit_page_length=0,
		)
		for invoice in invoices:
			invoice.update(invoice_type=doctype, party=invoice.get(party))
		rows.extend(invoices)
	return rows


def get_columns(months, from_date, include_earlier, show_details):
	columns = [{"label": _("Cash Flow"), "fieldname": "flow", "fieldtype": "Data", "width": 280}]
	if show_details:
		columns.extend(
			[
				{"label": _("Invoice Type"), "fieldname": "invoice_type", "fieldtype": "Data", "hidden": 1},
				{
					"label": _("Invoice"),
					"fieldname": "invoice",
					"fieldtype": "Dynamic Link",
					"options": "invoice_type",
					"width": 180,
				},
				{"label": _("Party Type"), "fieldname": "party_type", "fieldtype": "Data", "hidden": 1},
				{
					"label": _("Party"),
					"fieldname": "party",
					"fieldtype": "Dynamic Link",
					"options": "party_type",
					"width": 180,
				},
				{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 110},
				{"label": _("Due Date"), "fieldname": "due_date", "fieldtype": "Date", "width": 110},
			]
		)
	if include_earlier:
		columns.append(amount_column("earlier", _("Due before {0}").format(from_date)))
	columns.extend(
		amount_column(month_key(month), _(month.strftime("%b")) + month.strftime(" %Y")) for month in months
	)
	columns.extend(
		[
			amount_column("undated", _("No Due Date")),
			amount_column("total", _("Total")),
			{
				"label": _("Currency"),
				"fieldname": "currency",
				"fieldtype": "Link",
				"options": "Currency",
				"hidden": 1,
			},
		]
	)
	return columns


def amount_column(fieldname, label):
	return {
		"label": label,
		"fieldname": fieldname,
		"fieldtype": "Currency",
		"options": "currency",
		"width": 160,
	}
