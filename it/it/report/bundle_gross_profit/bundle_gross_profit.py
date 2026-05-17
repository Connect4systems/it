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
		_resolve_delivery_note_link(item)
		if _is_zero_value_sales_order_bom_component(item):
			continue

		components = _get_components_for_invoice_item(item)
		component_rows = []
		is_bundle = bool(components)

		if is_bundle:
			total_cost = 0
			for component in components:
				qty = abs(flt(component.get("qty")))
				avg_cost, cost_amount = _get_component_cost(component)
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
		else:
			average_cost, total_cost = _get_invoice_item_cost(item)

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
				"average_cost": None if is_bundle else average_cost,
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
		conditions.append(f"({_get_delivery_note_filter_condition()})")

	optional_fields = []
	for fieldname in (
		"stock_qty",
		"incoming_rate",
		"valuation_rate",
		"buying_amount",
		"base_amount",
		"sales_order",
		"so_detail",
	):
		if _has_column("Sales Invoice Item", fieldname):
			optional_fields.append(f"sii.{fieldname}")

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
			{", " + ", ".join(optional_fields) if optional_fields else ""}
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

	if not components and item.get("delivery_note") and item.get("sales_order"):
		components = _get_delivery_note_custom_bom_components(item)

	if not components:
		components = _get_sales_invoice_components(item.sales_invoice, item.sales_invoice_item, item.item_code)

	return components


def _resolve_delivery_note_link(item):
	if item.get("delivery_note"):
		return

	linked_dn = _find_delivery_note_for_invoice_item(item)
	if linked_dn:
		item.delivery_note = linked_dn.get("delivery_note")
		item.dn_detail = linked_dn.get("dn_detail")


def _find_delivery_note_for_invoice_item(item):
	lookups = []

	if item.get("sales_invoice"):
		for fieldname in ("against_sales_invoice", "sales_invoice"):
			if _has_column("Delivery Note Item", fieldname):
				lookups.append((f"dni.{fieldname} = %(sales_invoice)s", {}))

	if item.get("sales_order"):
		for fieldname in ("against_sales_order", "sales_order"):
			if _has_column("Delivery Note Item", fieldname):
				extra_conditions = {}
				if item.get("so_detail") and _has_column("Delivery Note Item", "so_detail"):
					extra_conditions["so_detail"] = item.get("so_detail")
				lookups.append((f"dni.{fieldname} = %(sales_order)s", extra_conditions))

	for condition, extra_conditions in lookups:
		conditions = [
			"dn.docstatus = 1",
			"dni.item_code = %(item_code)s",
			condition,
		]
		params = {
			"sales_invoice": item.get("sales_invoice"),
			"sales_order": item.get("sales_order"),
			"item_code": item.get("item_code"),
		}

		if extra_conditions.get("so_detail"):
			conditions.append("dni.so_detail = %(so_detail)s")
			params["so_detail"] = extra_conditions["so_detail"]

		row = frappe.db.sql(
			f"""
			SELECT dni.parent AS delivery_note, dni.name AS dn_detail
			FROM `tabDelivery Note Item` dni
			INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
			WHERE {" AND ".join(conditions)}
			ORDER BY dn.posting_date DESC, dn.name DESC, dni.idx
			LIMIT 1
			""",
			params,
			as_dict=True,
		)
		if row:
			return row[0]

	if item.get("sales_order"):
		for fieldname in ("against_sales_order", "sales_order"):
			if not _has_column("Delivery Note Item", fieldname):
				continue

			row = frappe.db.sql(
				f"""
				SELECT dni.parent AS delivery_note, dni.name AS dn_detail
				FROM `tabDelivery Note Item` dni
				INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
				WHERE dn.docstatus = 1
					AND dni.{fieldname} = %(sales_order)s
				ORDER BY dn.posting_date DESC, dn.name DESC, dni.idx
				LIMIT 1
				""",
				{"sales_order": item.get("sales_order")},
				as_dict=True,
			)
			if row:
				return row[0]

	return None


def _get_delivery_note_filter_condition():
	conditions = ["sii.delivery_note = %(delivery_note)s"]

	for fieldname in ("against_sales_invoice", "sales_invoice"):
		if _has_column("Delivery Note Item", fieldname):
			conditions.append(
				f"""
				EXISTS (
					SELECT 1
					FROM `tabDelivery Note Item` dni_filter
					WHERE dni_filter.parent = %(delivery_note)s
						AND dni_filter.{fieldname} = si.name
						AND dni_filter.item_code = sii.item_code
				)
				"""
			)

	if _has_column("Sales Invoice Item", "sales_order"):
		for fieldname in ("against_sales_order", "sales_order"):
			if _has_column("Delivery Note Item", fieldname):
				conditions.append(
					f"""
					EXISTS (
						SELECT 1
						FROM `tabDelivery Note Item` dni_filter
						WHERE dni_filter.parent = %(delivery_note)s
							AND dni_filter.{fieldname} = sii.sales_order
							AND IFNULL(sii.sales_order, '') != ''
							AND dni_filter.item_code = sii.item_code
					)
					"""
				)

	return " OR ".join(conditions)


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


def _get_delivery_note_custom_bom_components(item):
	component_items = _get_sales_order_custom_bom_item_codes(item.get("sales_order"), item.get("item_code"))
	if not component_items:
		return []

	fields = ["dni.name", "dni.parent", "'Delivery Note' AS parenttype", "dni.item_code", "dni.item_name", "dni.qty"]
	for fieldname in ("warehouse", "incoming_rate", "rate", "valuation_rate"):
		if _has_column("Delivery Note Item", fieldname):
			fields.append(f"dni.{fieldname}")

	return frappe.db.sql(
		f"""
		SELECT {", ".join(fields)}
		FROM `tabDelivery Note Item` dni
		INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
		WHERE dn.docstatus = 1
			AND dni.parent = %(delivery_note)s
			AND dni.item_code IN %(component_items)s
		ORDER BY dni.idx
		""",
		{
			"delivery_note": item.get("delivery_note"),
			"component_items": tuple(component_items),
		},
		as_dict=True,
	)


def _get_sales_order_custom_bom_item_codes(sales_order, parent_item):
	if not sales_order or not parent_item:
		return []

	rows = frappe.db.sql(
		"""
		SELECT item
		FROM `tabDelivery BOM`
		WHERE parenttype = 'Sales Order'
			AND parent = %(sales_order)s
			AND custom_parent_product = %(parent_item)s
			AND IFNULL(item, '') != ''
		ORDER BY idx
		""",
		{"sales_order": sales_order, "parent_item": parent_item},
		as_dict=True,
	)

	return [d.item for d in rows]


def _is_zero_value_sales_order_bom_component(item):
	if not item.get("sales_order") or flt(item.get("base_net_amount") or item.get("net_amount") or item.get("amount")):
		return False

	if not _has_column("Sales Invoice Item", "sales_order"):
		return False

	rows = frappe.db.sql(
		"""
		SELECT DISTINCT bom.custom_parent_product
		FROM `tabDelivery BOM` bom
		INNER JOIN `tabSales Invoice Item` parent_sii
			ON parent_sii.parent = %(sales_invoice)s
			AND parent_sii.item_code = bom.custom_parent_product
			AND parent_sii.sales_order = %(sales_order)s
		WHERE bom.parenttype = 'Sales Order'
			AND bom.parent = %(sales_order)s
			AND bom.item = %(item_code)s
			AND IFNULL(bom.custom_parent_product, '') != ''
			AND bom.custom_parent_product != %(item_code)s
		LIMIT 1
		""",
		{
			"sales_invoice": item.get("sales_invoice"),
			"sales_order": item.get("sales_order"),
			"item_code": item.get("item_code"),
		},
		as_dict=True,
	)

	return bool(rows)


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


def _get_component_cost(component):
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


def _get_invoice_item_cost(item):
	qty = abs(flt(item.get("stock_qty") or item.get("qty")))
	if not qty:
		return 0, 0

	avg_cost = _get_invoice_item_stock_ledger_average(item)

	if not avg_cost and flt(item.get("buying_amount")):
		return flt(item.get("buying_amount")) / qty, flt(item.get("buying_amount"))

	if not avg_cost:
		for fieldname in ("incoming_rate", "valuation_rate"):
			if item.get(fieldname):
				avg_cost = flt(item.get(fieldname))
				break

	if not avg_cost:
		avg_cost = flt(
			frappe.db.get_value("Item", item.get("item_code"), "valuation_rate")
			or frappe.db.get_value("Item", item.get("item_code"), "last_purchase_rate")
		)

	return avg_cost, qty * avg_cost


def _get_invoice_item_stock_ledger_average(item):
	if item.get("delivery_note"):
		conditions = [
			"voucher_type = 'Delivery Note'",
			"voucher_no = %(delivery_note)s",
			"item_code = %(item_code)s",
			"actual_qty < 0",
		]
		params = {
			"delivery_note": item.get("delivery_note"),
			"dn_detail": item.get("dn_detail"),
			"item_code": item.get("item_code"),
		}

		if _has_column("Stock Ledger Entry", "voucher_detail_no") and item.get("dn_detail"):
			detail_cost = _query_stock_ledger_average(conditions + ["voucher_detail_no = %(dn_detail)s"], params)
			if detail_cost:
				return detail_cost

		cost = _query_stock_ledger_average(conditions, params)
		if cost:
			return cost

	conditions = [
		"voucher_type = 'Sales Invoice'",
		"voucher_no = %(sales_invoice)s",
		"item_code = %(item_code)s",
		"actual_qty < 0",
	]
	params = {
		"sales_invoice": item.get("sales_invoice"),
		"sales_invoice_item": item.get("sales_invoice_item"),
		"item_code": item.get("item_code"),
	}

	if _has_column("Stock Ledger Entry", "voucher_detail_no") and item.get("sales_invoice_item"):
		detail_cost = _query_stock_ledger_average(
			conditions + ["voucher_detail_no = %(sales_invoice_item)s"],
			params,
		)
		if detail_cost:
			return detail_cost

	return _query_stock_ledger_average(conditions, params)


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
