# Copyright (c) 2026, Connect 4 Systems
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt


def execute(filters=None):
	filters = frappe._dict(filters or {})
	_validate_filters(filters)

	columns = get_columns()
	data = get_data(filters)
	chart = get_chart(data)
	report_summary = get_report_summary(data)

	return columns, data, None, chart, report_summary


def _validate_filters(filters):
	if not filters.get("company"):
		frappe.throw(_("Company is required"))
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("From Date and To Date are required"))


def get_columns():
	return [
		{"label": _("Row"), "fieldname": "row_label", "fieldtype": "Data", "width": 260},
		{"label": _("Sales Invoice"), "fieldname": "sales_invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 150},
		{"label": _("Delivery Note"), "fieldname": "delivery_note", "fieldtype": "Link", "options": "Delivery Note", "width": 150},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 110},
		{"label": _("Customer"), "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 180},
		{"label": _("Sales Partner"), "fieldname": "sales_partner", "fieldtype": "Link", "options": "Sales Partner", "width": 150},
		{"label": _("Parent Item"), "fieldname": "parent_item", "fieldtype": "Link", "options": "Item", "width": 160},
		{"label": _("Component Item"), "fieldname": "component_item", "fieldtype": "Link", "options": "Item", "width": 160},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 90},
		{"label": _("Average Cost"), "fieldname": "average_cost", "fieldtype": "Currency", "width": 120},
		{"label": _("Cost Amount"), "fieldname": "cost_amount", "fieldtype": "Currency", "width": 130},
		{"label": _("Sales Amount"), "fieldname": "sales_amount", "fieldtype": "Currency", "width": 130},
		{"label": _("Gross Profit"), "fieldname": "gross_profit", "fieldtype": "Currency", "width": 130},
		{"label": _("Gross Profit %"), "fieldname": "gross_profit_percent", "fieldtype": "Percent", "width": 120},
	]


def get_data(filters):
	rows = []
	invoice_items = _get_sales_invoice_items(filters)

	for item in invoice_items:
		components = _get_components_for_invoice_item(item)
		if not components:
			continue

		component_rows = []
		total_cost = 0
		for component in components:
			qty = abs(flt(component.get("qty")))
			avg_cost, cost_amount = _get_component_cost(component, item)
			total_cost += cost_amount

			component_rows.append(
				{
					"indent": 1,
					"row_label": component.get("item_name") or component.get("item_code"),
					"sales_invoice": item.sales_invoice,
					"delivery_note": item.delivery_note,
					"posting_date": item.posting_date,
					"customer": item.customer,
					"sales_partner": item.sales_partner,
					"parent_item": item.item_code,
					"component_item": component.get("item_code"),
					"qty": qty,
					"average_cost": avg_cost,
					"cost_amount": cost_amount,
					"sales_amount": 0,
					"gross_profit": None,
					"gross_profit_percent": None,
				}
			)

		sales_amount = flt(item.base_net_amount or item.net_amount or item.amount)
		gross_profit = sales_amount - total_cost
		gross_profit_percent = (gross_profit / sales_amount * 100) if sales_amount else 0

		rows.append(
			{
				"indent": 0,
				"row_label": item.item_name or item.item_code,
				"sales_invoice": item.sales_invoice,
				"delivery_note": item.delivery_note,
				"posting_date": item.posting_date,
				"customer": item.customer,
				"sales_partner": item.sales_partner,
				"parent_item": item.item_code,
				"component_item": None,
				"qty": flt(item.qty),
				"average_cost": None,
				"cost_amount": total_cost,
				"sales_amount": sales_amount,
				"gross_profit": gross_profit,
				"gross_profit_percent": gross_profit_percent,
			}
		)
		rows.extend(component_rows)

	return rows


def _get_sales_invoice_items(filters):
	conditions = [
		"si.docstatus = 1",
		"si.company = %(company)s",
		"si.posting_date BETWEEN %(from_date)s AND %(to_date)s",
	]

	if filters.get("customer"):
		conditions.append("si.customer = %(customer)s")
	if filters.get("sales_partner"):
		conditions.append("si.sales_partner = %(sales_partner)s")
	if filters.get("parent_item"):
		conditions.append("sii.item_code = %(parent_item)s")
	if filters.get("sales_invoice"):
		conditions.append("si.name = %(sales_invoice)s")
	if filters.get("delivery_note"):
		conditions.append("sii.delivery_note = %(delivery_note)s")

	return frappe.db.sql(
		f"""
		SELECT
			si.name AS sales_invoice,
			si.posting_date,
			si.customer,
			si.sales_partner,
			sii.name AS sales_invoice_item,
			sii.item_code,
			sii.item_name,
			sii.qty,
			sii.amount,
			sii.net_amount,
			sii.base_net_amount,
			sii.delivery_note,
			sii.dn_detail
		FROM `tabSales Invoice` si
		INNER JOIN `tabSales Invoice Item` sii ON sii.parent = si.name
		WHERE {" AND ".join(conditions)}
		ORDER BY si.posting_date, si.name, sii.idx
		""",
		filters,
		as_dict=True,
	)


def _get_components_for_invoice_item(item):
	components = []

	if item.delivery_note:
		components = _get_delivery_note_components(item.delivery_note, item.dn_detail, item.item_code)

	if not components:
		components = _get_sales_invoice_components(item.sales_invoice, item.sales_invoice_item, item.item_code)

	return components


def _get_delivery_note_components(delivery_note, dn_detail, parent_item):
	conditions = ["parent = %(delivery_note)s", "parenttype = 'Delivery Note'"]
	params = {"delivery_note": delivery_note, "dn_detail": dn_detail, "parent_item": parent_item}

	if _has_column("Packed Item", "parent_detail_docname") and dn_detail:
		conditions.append("parent_detail_docname = %(dn_detail)s")
	elif _has_column("Packed Item", "parent_item"):
		conditions.append("parent_item = %(parent_item)s")

	return _get_packed_items(conditions, params)


def _get_sales_invoice_components(sales_invoice, sales_invoice_item, parent_item):
	conditions = ["parent = %(sales_invoice)s", "parenttype = 'Sales Invoice'"]
	params = {
		"sales_invoice": sales_invoice,
		"sales_invoice_item": sales_invoice_item,
		"parent_item": parent_item,
	}

	if _has_column("Packed Item", "parent_detail_docname"):
		conditions.append("parent_detail_docname = %(sales_invoice_item)s")
	elif _has_column("Packed Item", "parent_item"):
		conditions.append("parent_item = %(parent_item)s")

	return _get_packed_items(conditions, params)


def _get_packed_items(conditions, params):
	fields = ["name", "parent", "parenttype", "item_code", "item_name", "qty"]
	for fieldname in ("warehouse", "incoming_rate", "rate", "valuation_rate", "parent_item", "parent_detail_docname"):
		if _has_column("Packed Item", fieldname):
			fields.append(fieldname)

	return frappe.db.sql(
		f"""
		SELECT {", ".join(fields)}
		FROM `tabPacked Item`
		WHERE {" AND ".join(conditions)}
		ORDER BY idx
		""",
		params,
		as_dict=True,
	)


def _get_component_cost(component, invoice_item):
	qty = abs(flt(component.get("qty")))
	if not qty:
		return 0, 0

	avg_cost = _get_stock_ledger_average(component)

	if not avg_cost:
		for fieldname in ("incoming_rate", "valuation_rate", "rate"):
			if component.get(fieldname):
				avg_cost = flt(component.get(fieldname))
				break

	if not avg_cost:
		avg_cost = flt(
			frappe.db.get_value("Item", component.get("item_code"), "valuation_rate")
			or frappe.db.get_value("Item", component.get("item_code"), "last_purchase_rate")
		)

	return avg_cost, qty * avg_cost


def _get_stock_ledger_average(component):
	if component.get("parenttype") != "Delivery Note":
		return 0

	conditions = [
		"voucher_type = 'Delivery Note'",
		"voucher_no = %(delivery_note)s",
		"item_code = %(item_code)s",
		"actual_qty < 0",
	]
	params = {
		"delivery_note": component.get("parent"),
		"item_code": component.get("item_code"),
		"packed_item": component.get("name"),
	}

	if _has_column("Stock Ledger Entry", "voucher_detail_no") and component.get("name"):
		detail_cost = _query_stock_ledger_average(conditions + ["voucher_detail_no = %(packed_item)s"], params)
		if detail_cost:
			return detail_cost

	return _query_stock_ledger_average(conditions, params)


def _query_stock_ledger_average(conditions, params):
	row = frappe.db.sql(
		f"""
		SELECT
			SUM(ABS(actual_qty)) AS qty,
			SUM(ABS(stock_value_difference)) AS amount
		FROM `tabStock Ledger Entry`
		WHERE {" AND ".join(conditions)}
		""",
		params,
		as_dict=True,
	)

	if not row:
		return 0

	qty = flt(row[0].get("qty"))
	amount = flt(row[0].get("amount"))
	return (amount / qty) if qty else 0


def _has_column(doctype, fieldname):
	try:
		return bool(frappe.db.has_column(doctype, fieldname))
	except Exception:
		return False


def get_chart(data):
	parent_rows = [d for d in data if not d.get("indent")]
	return {
		"data": {
			"labels": [d.get("row_label") for d in parent_rows[:20]],
			"datasets": [
				{"name": _("Sales Amount"), "values": [flt(d.get("sales_amount")) for d in parent_rows[:20]]},
				{"name": _("Cost Amount"), "values": [flt(d.get("cost_amount")) for d in parent_rows[:20]]},
			],
		},
		"type": "bar",
	}


def get_report_summary(data):
	parent_rows = [d for d in data if not d.get("indent")]
	sales_amount = sum(flt(d.get("sales_amount")) for d in parent_rows)
	cost_amount = sum(flt(d.get("cost_amount")) for d in parent_rows)
	gross_profit = sales_amount - cost_amount
	gross_profit_percent = (gross_profit / sales_amount * 100) if sales_amount else 0

	return [
		{"value": sales_amount, "indicator": "Blue", "label": _("Sales Amount"), "datatype": "Currency"},
		{"value": cost_amount, "indicator": "Orange", "label": _("Bundle Cost"), "datatype": "Currency"},
		{"value": gross_profit, "indicator": "Green" if gross_profit >= 0 else "Red", "label": _("Gross Profit"), "datatype": "Currency"},
		{"value": gross_profit_percent, "indicator": "Green" if gross_profit >= 0 else "Red", "label": _("Gross Profit %"), "datatype": "Percent"},
	]
