# Copyright (c) 2026, Connect 4 Systems
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import flt, get_datetime

from .stock_costs import invoice_sources, ledger_cost, stock_qty


def execute(filters=None):
	filters = frappe._dict(filters or {})
	_validate_filters(filters)

	columns = get_columns()
	data = get_data(filters)
	if filters.get("reconcile_cogs"):
		from .cogs_reconciliation import execute_reconciliation

		return execute_reconciliation(filters, data)
	chart = get_chart(data)
	report_summary = get_report_summary(data)
	message = _(
		"Cost Amount uses actual signed stock values. Average Cost is reference only. "
		"Rows requiring review have incomplete costs and no profit calculation. "
		"Use Reconcile COGS to compare voucher totals with the General Ledger."
	)
	# Component costs already appear in parents; never generate an additional tree total.
	return columns, data, message, chart, report_summary, True


def _validate_filters(filters):
	if not filters.get("company"):
		frappe.throw(_("Company is required"))
	if not filters.get("from_date") or not filters.get("to_date"):
		frappe.throw(_("From Date and To Date are required"))
	if get_datetime(filters.from_date) > get_datetime(filters.to_date):
		frappe.throw(_("From Date must not be after To Date"))
	if filters.get("reconcile_cogs") and any(
		filters.get(key)
		for key in ("customer", "sales_partner", "parent_item", "sales_invoice", "delivery_note")
	):
		frappe.throw(
			_("Clear customer, sales partner, item, invoice and delivery filters to reconcile company COGS")
		)


def get_columns():
	return [
		{"label": _("Row"), "fieldname": "row_label", "fieldtype": "Data", "width": 260},
		{
			"label": _("Sales Invoice"),
			"fieldname": "sales_invoice",
			"fieldtype": "Link",
			"options": "Sales Invoice",
			"width": 150,
		},
		{
			"label": _("Delivery Note"),
			"fieldname": "delivery_note",
			"fieldtype": "Link",
			"options": "Delivery Note",
			"width": 150,
		},
		{"label": _("Posting Date"), "fieldname": "posting_date", "fieldtype": "Date", "width": 110},
		{
			"label": _("Customer"),
			"fieldname": "customer",
			"fieldtype": "Link",
			"options": "Customer",
			"width": 180,
		},
		{
			"label": _("Sales Partner"),
			"fieldname": "sales_partner",
			"fieldtype": "Link",
			"options": "Sales Partner",
			"width": 150,
		},
		{
			"label": _("Parent Item"),
			"fieldname": "parent_item",
			"fieldtype": "Link",
			"options": "Item",
			"width": 160,
		},
		{
			"label": _("Component Item"),
			"fieldname": "component_item",
			"fieldtype": "Link",
			"options": "Item",
			"width": 160,
		},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 90},
		{"label": _("Average Cost"), "fieldname": "average_cost", "fieldtype": "Currency", "width": 120},
		{"label": _("Actual Cost"), "fieldname": "actual_cost", "fieldtype": "Currency", "width": 120},
		{"label": _("Cost Basis"), "fieldname": "cost_basis", "fieldtype": "Data", "width": 240},
		{"label": _("Cost Status"), "fieldname": "cost_status", "fieldtype": "Data", "width": 140},
		{"label": _("Cost Amount"), "fieldname": "cost_amount", "fieldtype": "Currency", "width": 130},
		{"label": _("Sales Amount"), "fieldname": "sales_amount", "fieldtype": "Currency", "width": 130},
		{"label": _("Gross Profit"), "fieldname": "gross_profit", "fieldtype": "Currency", "width": 130},
		{
			"label": _("Gross Profit %"),
			"fieldname": "gross_profit_percent",
			"fieldtype": "Percent",
			"width": 120,
		},
	]


def get_data(filters):
	rows = []
	cost_context = {"has_serial_no": {}, "outgoing_cutoffs": {}, "serial_rates": {}, "valuation_rates": {}}
	for item in _get_sales_invoice_items(filters):
		non_stock = _is_non_stock_service(item)
		sources, issue = ([], None) if non_stock else invoice_sources(item)
		if (
			filters.get("delivery_note")
			and item.get("delivery_note") != filters.delivery_note
			and not any(s.get("delivery_note") == filters.delivery_note for s in sources)
		):
			continue
		component_rows = []
		allocations = []
		total_cost = 0
		weighted_average = 0
		costed_qty = 0
		is_bundle = False
		basis = [_("No stock movement; non-stock item")] if non_stock else []
		for source in sources:
			components = _get_components_for_invoice_item(source)
			is_bundle = is_bundle or bool(components)
			cost_rows = components or [source]
			for cost_row in cost_rows:
				cost_row = frappe._dict(cost_row)
				cost_row.company = item.company
				cost_row.posting_date = item.posting_date
				if components:
					# Invoice packed rows already represent the invoiced quantity.
					if cost_row.parenttype == "Delivery Note":
						cost_row.stock_qty = stock_qty(cost_row) * source.allocation_ratio
				actual, amount, cost_basis = ledger_cost(cost_row)
				if source.get("allocation_basis"):
					cost_basis = source.allocation_basis + "; " + cost_basis
				average = None
				if amount is not None:
					average = _get_item_cost(cost_row, abs(stock_qty(cost_row)), cost_context)[0]
					total_cost += amount
					voucher_type = "Sales Invoice" if source.get("update_stock") else "Delivery Note"
					voucher_no = source.sales_invoice if source.get("update_stock") else source.delivery_note
					allocations.append(
						{
							"voucher_type": voucher_type,
							"voucher_no": voucher_no,
							"cost": amount,
							"detail": cost_row.get("parent_detail_docname")
							or cost_row.get("name")
							or source.get("dn_detail")
							or source.sales_invoice_item,
							"item_code": cost_row.item_code,
							"qty": stock_qty(cost_row),
							"component": bool(components),
						}
					)
				else:
					issue = issue or cost_basis
				basis.append(cost_basis)
				if components:
					component_rows.append(
						{
							"indent": 1,
							"row_label": cost_row.get("item_name") or cost_row.item_code,
							"sales_invoice": item.sales_invoice,
							"delivery_note": source.get("delivery_note"),
							"posting_date": item.posting_date,
							"customer": item.customer,
							"sales_partner": item.sales_partner,
							"parent_item": item.item_code,
							"component_item": cost_row.item_code,
							"qty": stock_qty(cost_row),
							"average_cost": average,
							"actual_cost": actual,
							"cost_basis": cost_basis,
							"cost_amount": amount,
							"sales_amount": 0,
							"gross_profit": None,
							"gross_profit_percent": None,
							"cost_status": _("Review required") if amount is None else _("Verified"),
						}
					)
				elif amount is not None:
					weighted_average += flt(average) * abs(stock_qty(source))
					costed_qty += abs(stock_qty(source))
		sales_amount = flt(item.base_net_amount)
		gross_profit = None if issue else sales_amount - total_cost
		delivery_notes = sorted(
			{s.delivery_note for s in sources if s.get("delivery_note") and not s.get("update_stock")}
		)
		bundle_basis = _("Sum of actual component costs")
		if any(s.get("allocation_basis") for s in sources):
			bundle_basis += "; " + _("Sales Order item allocation (posting order)")
		rows.append(
			{
				"indent": 0,
				"row_label": item.item_name or item.item_code,
				"sales_invoice": item.sales_invoice,
				"delivery_note": delivery_notes[0] if len(delivery_notes) == 1 else None,
				"posting_date": item.posting_date,
				"customer": item.customer,
				"sales_partner": item.sales_partner,
				"parent_item": item.item_code,
				"component_item": None,
				"qty": stock_qty(item),
				"average_cost": weighted_average / costed_qty if costed_qty and not is_bundle else None,
				"actual_cost": total_cost / stock_qty(item)
				if stock_qty(item) and not is_bundle and not issue
				else None,
				"cost_basis": issue or (bundle_basis if is_bundle else "; ".join(dict.fromkeys(basis))),
				"cost_amount": total_cost if allocations or not issue else None,
				"sales_amount": sales_amount,
				"gross_profit": gross_profit,
				"gross_profit_percent": (gross_profit / sales_amount * 100 if sales_amount else 0)
				if gross_profit is not None
				else None,
				"cost_status": _("Review required") if issue else _("Verified"),
				"cost_incomplete": bool(issue),
				"stock_allocations": allocations,
				"_bom_component": _is_zero_value_sales_order_bom_component(item),
			}
		)
		rows.extend(component_rows)
	return _remove_represented_component_rows(rows)


def _is_non_stock_service(item):
	# Non-stock services have no inventory cost; manual expenses remain in reconciliation.
	if not item.get("item_code"):
		return True
	if frappe.get_cached_value("Item", item.item_code, "is_stock_item"):
		return False
	return not (
		frappe.db.exists("Product Bundle", {"new_item_code": item.item_code})
		or _get_sales_invoice_components(item.sales_invoice, item.sales_invoice_item, item.item_code)
		or _get_sales_order_custom_bom_item_codes(item.get("sales_order"), item.item_code)
	)


def _remove_represented_component_rows(rows):
	"""Hide a zero-sales custom BOM row only when its exact cost is already in a parent."""
	from collections import defaultdict

	represented = defaultdict(float)

	def key(row, allocation):
		return (
			row["sales_invoice"],
			allocation["voucher_type"],
			allocation["voucher_no"],
			allocation["detail"],
			allocation["item_code"],
		)

	for row in rows:
		for allocation in row.get("stock_allocations", []):
			if allocation.get("component"):
				represented[key(row, allocation)] += allocation["qty"]
	result = []
	for row in rows:
		is_component = row.pop("_bom_component", False)
		allocations = row.get("stock_allocations", [])
		if (
			is_component
			and allocations
			and not row.get("cost_incomplete")
			and all(abs(represented.get(key(row, a), 0) - a["qty"]) < 0.000001 for a in allocations)
		):
			continue
		if is_component and any(key(row, a) in represented for a in allocations):
			# Do not count overlapping quantities again when the custom BOM mapping is inconsistent.
			row.update(
				cost_amount=None,
				actual_cost=None,
				gross_profit=None,
				gross_profit_percent=None,
				stock_allocations=[],
				cost_incomplete=True,
				cost_status=_("Review required"),
				cost_basis=_("Custom BOM and component invoice quantities overlap; review allocation"),
			)
		result.append(row)
	return result


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
		"base_amount",
		"sales_order",
		"so_detail",
		"warehouse",
		"serial_no",
		"serial_and_batch_bundle",
		"batch_no",
	):
		if _has_column("Sales Invoice Item", fieldname):
			optional_fields.append(f"sii.{fieldname}")

	return frappe.db.sql(
		f"""
		SELECT
			si.name AS sales_invoice,
			si.company,
			si.update_stock,
			si.is_return,
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

	if not components and not item.get("delivery_note"):
		components = _get_sales_invoice_components(
			item.sales_invoice, item.sales_invoice_item, item.item_code
		)

	return components


def _get_delivery_note_filter_condition():
	return """sii.delivery_note = %(delivery_note)s OR EXISTS (
		SELECT 1 FROM `tabDelivery Note Item` dni_filter
		INNER JOIN `tabDelivery Note` dn_filter ON dn_filter.name = dni_filter.parent
		WHERE dn_filter.docstatus = 1 AND dn_filter.company = si.company
			AND dni_filter.parent = %(delivery_note)s
			AND dni_filter.item_code = sii.item_code
			AND (dni_filter.si_detail = sii.name
				OR (dni_filter.against_sales_invoice = si.name AND IFNULL(dni_filter.si_detail, '') = ''))
	) OR EXISTS (
		SELECT 1 FROM `tabDelivery Note Item` order_dni
		INNER JOIN `tabDelivery Note` order_dn ON order_dn.name = order_dni.parent
		WHERE order_dn.docstatus = 1 AND order_dn.company = si.company
			AND order_dni.parent = %(delivery_note)s AND order_dni.item_code = sii.item_code
			AND order_dni.against_sales_order = sii.sales_order AND order_dni.so_detail = sii.so_detail
			AND IFNULL(sii.so_detail, '') != '' AND sii.stock_qty > 0
			AND IFNULL(sii.delivery_note, '') = '' AND IFNULL(sii.dn_detail, '') = ''
	)"""


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

	fields = [
		"dni.name",
		"dni.parent",
		"'Delivery Note' AS parenttype",
		"dni.item_code",
		"dni.item_name",
		"dni.qty",
	]
	for fieldname in (
		"warehouse",
		"incoming_rate",
		"rate",
		"valuation_rate",
		"stock_qty",
		"serial_no",
		"serial_and_batch_bundle",
		"batch_no",
	):
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
			AND EXISTS (
				SELECT 1 FROM `tabSales Invoice Item` component_sii
				WHERE component_sii.parent = %(sales_invoice)s
					AND component_sii.dn_detail = dni.name
					AND component_sii.item_code = dni.item_code
					AND component_sii.base_net_amount = 0
			)
			AND (SELECT COUNT(DISTINCT bom.custom_parent_product) FROM `tabDelivery BOM` bom
				WHERE bom.parent = %(sales_order)s AND bom.parenttype = 'Sales Order'
					AND bom.item = dni.item_code) = 1
			AND (SELECT COUNT(*) FROM `tabSales Invoice Item` parent_sii
				WHERE parent_sii.parent = %(sales_invoice)s AND parent_sii.item_code = %(parent_item)s) = 1
		ORDER BY dni.idx
		""",
		{
			"delivery_note": item.get("delivery_note"),
			"component_items": tuple(component_items),
			"sales_invoice": item.get("sales_invoice"),
			"sales_order": item.get("sales_order"),
			"parent_item": item.get("item_code"),
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
	if not item.get("sales_order") or flt(
		item.get("base_net_amount") or item.get("net_amount") or item.get("amount")
	):
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
	for fieldname in (
		"warehouse",
		"incoming_rate",
		"rate",
		"valuation_rate",
		"stock_qty",
		"serial_no",
		"serial_and_batch_bundle",
		"batch_no",
		"parent_item",
		"parent_detail_docname",
	):
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


def _get_component_cost(component, cost_context):
	qty = abs(flt(component.get("stock_qty") or component.get("qty")))
	return _get_item_cost(component, qty, cost_context)


def _get_invoice_item_cost(item, cost_context):
	qty = abs(flt(item.get("stock_qty") or item.get("qty")))
	return _get_item_cost(item, qty, cost_context)


def _get_item_cost(row, qty, cost_context):
	if not qty:
		return 0, 0, _("Zero quantity")

	if _item_has_serial_no(row.get("item_code"), cost_context):
		return _get_serialized_item_cost(row, qty, cost_context)

	average_cost = _get_fifo_stock_ledger_average(row)
	if average_cost is None:
		return 0, 0, _("FIFO cost unavailable")

	return average_cost, qty * average_cost, _("FIFO (Stock Ledger)")


def _item_has_serial_no(item_code, cost_context):
	if not item_code:
		return False

	cache = cost_context["has_serial_no"]
	if item_code not in cache:
		cache[item_code] = bool(frappe.get_cached_value("Item", item_code, "has_serial_no"))

	return cache[item_code]


def _get_serialized_item_cost(row, qty, cost_context):
	serial_numbers = _get_outgoing_serial_numbers(row)
	if not serial_numbers:
		return 0, 0, _("Missing serial numbers / Purchase Receipt cost")

	outgoing_cutoff = _get_outgoing_cutoff(row, cost_context)
	total_cost = 0
	missing_serials = []
	purchase_receipts = set()
	valuation_dates = set()

	for serial_no in serial_numbers:
		cache_key = (row.get("item_code"), serial_no, str(outgoing_cutoff or ""))
		if cache_key not in cost_context["serial_rates"]:
			cost_context["serial_rates"][cache_key] = _get_purchase_receipt_serial_rate(
				row.get("item_code"), serial_no, outgoing_cutoff
			)

		serial_rate = cost_context["serial_rates"][cache_key]
		rate, purchase_receipt = serial_rate if serial_rate else (None, None)
		if not abs(flt(rate)):
			valuation_rate = _get_cached_closest_valuation_rate(row, outgoing_cutoff, cost_context)
			if not valuation_rate:
				missing_serials.append(serial_no)
				continue
			rate, valuation_date = valuation_rate
			valuation_dates.add(str(valuation_date))

		total_cost += abs(flt(rate))
		if purchase_receipt:
			purchase_receipts.add(purchase_receipt)

	serial_qty = len(serial_numbers)
	average_cost = total_cost / serial_qty
	cost_amount = qty * average_cost
	basis_parts = [_("Serial Purchase Receipt")]

	if purchase_receipts:
		basis_parts.append(", ".join(sorted(purchase_receipts)))
	if valuation_dates:
		basis_parts.append(
			_("Closest Stock Ledger valuation ({0})").format(", ".join(sorted(valuation_dates)))
		)
	if flt(serial_qty) != flt(qty):
		basis_parts.append(_("serial quantity {0}, stock quantity {1}").format(serial_qty, qty))
	if missing_serials:
		basis_parts.append(_("missing: {0}").format(", ".join(missing_serials)))

	return average_cost, cost_amount, "; ".join(basis_parts)


def _get_cached_closest_valuation_rate(row, outgoing_cutoff, cost_context):
	reference_datetime = outgoing_cutoff
	if not reference_datetime and row.get("posting_date"):
		reference_datetime = get_datetime(row.get("posting_date"))
	if not reference_datetime:
		return None

	cache = cost_context.setdefault("valuation_rates", {})
	cache_key = (row.get("item_code"), row.get("warehouse"), str(reference_datetime))
	if cache_key not in cache:
		cache[cache_key] = _get_closest_valuation_rate(
			row.get("item_code"), reference_datetime, row.get("warehouse")
		)

	return cache[cache_key]


def _get_closest_valuation_rate(item_code, reference_datetime, warehouse=None):
	if not item_code or not reference_datetime or not _has_column("Stock Ledger Entry", "valuation_rate"):
		return None

	conditions = [
		"item_code = %(item_code)s",
		"ABS(IFNULL(valuation_rate, 0)) > 0",
	]
	params = {"item_code": item_code, "reference_datetime": reference_datetime}
	if _has_column("Stock Ledger Entry", "is_cancelled"):
		conditions.append("IFNULL(is_cancelled, 0) = 0")
	if warehouse and _has_column("Stock Ledger Entry", "warehouse"):
		conditions.append("warehouse = %(warehouse)s")
		params["warehouse"] = warehouse

	rows = frappe.db.sql(
		f"""
		SELECT valuation_rate, posting_date, posting_time
		FROM `tabStock Ledger Entry`
		WHERE {" AND ".join(conditions)}
		ORDER BY
			ABS(TIMESTAMPDIFF(SECOND,
				TIMESTAMP(posting_date, IFNULL(posting_time, '00:00:00')),
				%(reference_datetime)s
			)) ASC,
			posting_date DESC, posting_time DESC, creation DESC
		LIMIT 1
		""",
		params,
		as_dict=True,
	)
	if not rows:
		return None

	ledger_row = rows[0]
	rate = abs(flt(ledger_row.get("valuation_rate")))
	valuation_datetime = get_datetime(
		f"{ledger_row.get('posting_date')} {ledger_row.get('posting_time') or '00:00:00'}"
	)
	return rate, valuation_datetime


def _get_outgoing_serial_numbers(row):
	serial_numbers = []

	for ledger_row in _get_outgoing_stock_ledger_rows(row, ("serial_no", "serial_and_batch_bundle")):
		if ledger_row.get("serial_and_batch_bundle"):
			serial_numbers.extend(_get_bundle_serial_numbers(ledger_row.get("serial_and_batch_bundle")))
		serial_numbers.extend(_split_serial_numbers(ledger_row.get("serial_no")))

	if not serial_numbers:
		if row.get("serial_and_batch_bundle"):
			serial_numbers.extend(_get_bundle_serial_numbers(row.get("serial_and_batch_bundle")))
		serial_numbers.extend(_split_serial_numbers(row.get("serial_no")))

	return list(dict.fromkeys(serial_numbers))


def _get_bundle_serial_numbers(bundle):
	if not bundle or not _has_column("Serial and Batch Entry", "serial_no"):
		return []

	return [
		serial_no
		for serial_no in frappe.get_all(
			"Serial and Batch Entry",
			filters={"parent": bundle},
			order_by="idx",
			pluck="serial_no",
		)
		if serial_no
	]


def _split_serial_numbers(value):
	if not value:
		return []

	normalized = str(value).replace("\r", "\n").replace(",", "\n")
	return [serial_no.strip() for serial_no in normalized.split("\n") if serial_no.strip()]


def _get_purchase_receipt_serial_rate(item_code, serial_no, outgoing_cutoff=None):
	rate = _get_serial_bundle_purchase_receipt_rate(item_code, serial_no, outgoing_cutoff)
	if rate:
		return rate

	rate = _get_legacy_serial_purchase_receipt_rate(item_code, serial_no, outgoing_cutoff)
	if rate:
		return rate

	return _get_serial_master_purchase_receipt_rate(item_code, serial_no, outgoing_cutoff)


def _get_serial_bundle_purchase_receipt_rate(item_code, serial_no, outgoing_cutoff=None):
	required_columns = (
		_has_column("Serial and Batch Entry", "serial_no"),
		_has_column("Serial and Batch Bundle", "voucher_type"),
		_has_column("Serial and Batch Bundle", "voucher_no"),
	)
	if not all(required_columns):
		return None

	incoming_rate_field = (
		"sbe.incoming_rate" if _has_column("Serial and Batch Entry", "incoming_rate") else "0"
	)
	detail_field = (
		"bundle.voucher_detail_no" if _has_column("Serial and Batch Bundle", "voucher_detail_no") else "NULL"
	)
	conditions = [
		"bundle.voucher_type = 'Purchase Receipt'",
		"bundle.voucher_no = pr.name",
		"bundle.docstatus = 1",
		"pr.docstatus = 1",
		"bundle.item_code = %(item_code)s",
		"sbe.serial_no = %(serial_no)s",
	]

	if _has_column("Serial and Batch Bundle", "is_cancelled"):
		conditions.append("IFNULL(bundle.is_cancelled, 0) = 0")
	if _has_column("Serial and Batch Bundle", "type_of_transaction"):
		conditions.append("bundle.type_of_transaction = 'Inward'")
	if _has_column("Serial and Batch Entry", "qty"):
		conditions.append("sbe.qty > 0")
	if outgoing_cutoff and _has_column("Serial and Batch Bundle", "posting_datetime"):
		conditions.append("bundle.posting_datetime <= %(outgoing_cutoff)s")

	order_by = (
		"bundle.posting_datetime DESC, bundle.creation DESC"
		if _has_column("Serial and Batch Bundle", "posting_datetime")
		else "bundle.creation DESC"
	)
	rows = frappe.db.sql(
		f"""
		SELECT
			bundle.voucher_no AS purchase_receipt,
			{detail_field} AS purchase_receipt_item,
			{incoming_rate_field} AS incoming_rate
		FROM `tabSerial and Batch Entry` sbe
		INNER JOIN `tabSerial and Batch Bundle` bundle ON bundle.name = sbe.parent
		INNER JOIN `tabPurchase Receipt` pr ON pr.name = bundle.voucher_no
		WHERE {" AND ".join(conditions)}
		ORDER BY {order_by}
		LIMIT 1
		""",
		{
			"item_code": item_code,
			"serial_no": serial_no,
			"outgoing_cutoff": outgoing_cutoff,
		},
		as_dict=True,
	)
	if not rows:
		return None

	row = rows[0]
	rate = _get_purchase_receipt_ledger_rate(
		row.get("purchase_receipt"), row.get("purchase_receipt_item"), item_code
	)
	if not rate:
		rate = abs(flt(row.get("incoming_rate")))

	return rate, row.get("purchase_receipt")


def _get_legacy_serial_purchase_receipt_rate(item_code, serial_no, outgoing_cutoff=None):
	if not _has_column("Stock Ledger Entry", "serial_no"):
		return None

	conditions = [
		"sle.voucher_type = 'Purchase Receipt'",
		"sle.item_code = %(item_code)s",
		"sle.actual_qty > 0",
		"IFNULL(sle.serial_no, '') != ''",
		"sle.serial_no LIKE %(serial_pattern)s",
		"pr.docstatus = 1",
	]
	if _has_column("Stock Ledger Entry", "is_cancelled"):
		conditions.append("IFNULL(sle.is_cancelled, 0) = 0")
	if outgoing_cutoff:
		conditions.append(
			"(sle.posting_date < %(cutoff_date)s"
			" OR (sle.posting_date = %(cutoff_date)s AND sle.posting_time <= %(cutoff_time)s))"
		)

	fields = [
		"sle.voucher_no AS purchase_receipt",
		"sle.serial_no",
		"sle.actual_qty",
		"sle.stock_value_difference",
	]
	if _has_column("Stock Ledger Entry", "voucher_detail_no"):
		fields.append("sle.voucher_detail_no AS purchase_receipt_item")
	if _has_column("Stock Ledger Entry", "incoming_rate"):
		fields.append("sle.incoming_rate")
	if _has_column("Stock Ledger Entry", "valuation_rate"):
		fields.append("sle.valuation_rate")

	rows = frappe.db.sql(
		f"""
		SELECT {", ".join(fields)}
		FROM `tabStock Ledger Entry` sle
		INNER JOIN `tabPurchase Receipt` pr ON pr.name = sle.voucher_no
		WHERE {" AND ".join(conditions)}
		ORDER BY sle.posting_date DESC, sle.posting_time DESC, sle.creation DESC
		""",
		{
			"item_code": item_code,
			"serial_pattern": f"%{serial_no}%",
			"cutoff_date": outgoing_cutoff.date() if outgoing_cutoff else None,
			"cutoff_time": outgoing_cutoff.time() if outgoing_cutoff else None,
		},
		as_dict=True,
	)
	for row in rows:
		if serial_no not in _split_serial_numbers(row.get("serial_no")):
			continue

		rate = _get_incoming_ledger_row_rate(row)
		return rate, row.get("purchase_receipt")

	return None


def _get_serial_master_purchase_receipt_rate(item_code, serial_no, outgoing_cutoff=None):
	if not _has_column("Serial No", "purchase_document_no"):
		return None

	serial = frappe.db.get_value(
		"Serial No",
		{"name": serial_no, "item_code": item_code},
		["purchase_document_no", "purchase_rate"],
		as_dict=True,
	)
	if not serial or not serial.get("purchase_document_no"):
		return None

	is_submitted = frappe.db.get_value("Purchase Receipt", serial.get("purchase_document_no"), "docstatus")
	if is_submitted != 1:
		return None

	if outgoing_cutoff:
		receipt_posting = frappe.db.get_value(
			"Purchase Receipt",
			serial.get("purchase_document_no"),
			["posting_date", "posting_time"],
			as_dict=True,
		)
		if receipt_posting:
			receipt_datetime = get_datetime(
				f"{receipt_posting.get('posting_date')} {receipt_posting.get('posting_time') or '00:00:00'}"
			)
			if receipt_datetime > outgoing_cutoff:
				return None

	rate = _get_purchase_receipt_ledger_rate(serial.get("purchase_document_no"), None, item_code)
	if not rate:
		rate = abs(flt(serial.get("purchase_rate")))

	return rate, serial.get("purchase_document_no")


def _get_purchase_receipt_ledger_rate(purchase_receipt, purchase_receipt_item, item_code):
	if not purchase_receipt:
		return 0

	conditions = [
		"voucher_type = 'Purchase Receipt'",
		"voucher_no = %(purchase_receipt)s",
		"item_code = %(item_code)s",
		"actual_qty > 0",
	]
	params = {"purchase_receipt": purchase_receipt, "item_code": item_code}

	if _has_column("Stock Ledger Entry", "is_cancelled"):
		conditions.append("IFNULL(is_cancelled, 0) = 0")
	if purchase_receipt_item and _has_column("Stock Ledger Entry", "voucher_detail_no"):
		conditions.append("voucher_detail_no = %(purchase_receipt_item)s")
		params["purchase_receipt_item"] = purchase_receipt_item

	fields = ["actual_qty", "stock_value_difference"]
	for fieldname in ("incoming_rate", "valuation_rate"):
		if _has_column("Stock Ledger Entry", fieldname):
			fields.append(fieldname)

	rows = frappe.db.sql(
		f"""
		SELECT {", ".join(fields)}
		FROM `tabStock Ledger Entry`
		WHERE {" AND ".join(conditions)}
		ORDER BY posting_date DESC, posting_time DESC, creation DESC
		LIMIT 1
		""",
		params,
		as_dict=True,
	)
	if not rows:
		return 0

	return _get_incoming_ledger_row_rate(rows[0])


def _get_incoming_ledger_row_rate(row):
	rate = abs(flt(row.get("incoming_rate")))
	if rate:
		return rate

	qty = abs(flt(row.get("actual_qty")))
	amount = abs(flt(row.get("stock_value_difference")))
	if qty and amount:
		return amount / qty

	return abs(flt(row.get("valuation_rate")))


def _get_actual_stock_cost(row):
	"""Return the issued stock value per stock unit for the sale's ledger entries."""
	ledger_rows = _get_outgoing_stock_ledger_rows(row, ("actual_qty", "stock_value_difference"))
	total_qty = 0
	total_cost = 0
	for ledger_row in ledger_rows:
		qty = abs(flt(ledger_row.get("actual_qty")))
		if not qty:
			continue
		if ledger_row.get("stock_value_difference") is None:
			return None
		total_qty += qty
		total_cost += abs(flt(ledger_row.get("stock_value_difference")))

	return total_cost / total_qty if total_qty else None


def _get_fifo_stock_ledger_average(row):
	ledger_rows = _get_outgoing_stock_ledger_rows(
		row,
		("actual_qty", "outgoing_rate", "stock_value_difference", "valuation_rate"),
	)
	if not ledger_rows:
		return None

	total_qty = 0
	total_cost = 0
	for ledger_row in ledger_rows:
		qty = abs(flt(ledger_row.get("actual_qty")))
		if not qty:
			continue

		amount = abs(flt(ledger_row.get("stock_value_difference")))
		if not amount:
			rate = abs(flt(ledger_row.get("outgoing_rate")))
			amount = qty * rate
		if not amount:
			amount = qty * abs(flt(ledger_row.get("valuation_rate")))

		total_qty += qty
		total_cost += amount

	return total_cost / total_qty if total_qty else None


def _get_outgoing_cutoff(row, cost_context):
	reference = _get_outgoing_reference(row)
	if reference not in cost_context["outgoing_cutoffs"]:
		rows = _get_outgoing_stock_ledger_rows(row, ("posting_datetime", "posting_date", "posting_time"))
		cutoffs = []
		for ledger_row in rows:
			if ledger_row.get("posting_datetime"):
				cutoffs.append(get_datetime(ledger_row.get("posting_datetime")))
			elif ledger_row.get("posting_date"):
				cutoffs.append(
					get_datetime(
						f"{ledger_row.get('posting_date')} {ledger_row.get('posting_time') or '00:00:00'}"
					)
				)

		cost_context["outgoing_cutoffs"][reference] = max(cutoffs) if cutoffs else None

	return cost_context["outgoing_cutoffs"][reference]


def _get_outgoing_stock_ledger_rows(row, requested_fields):
	voucher_type, voucher_no, voucher_detail_no = _get_outgoing_reference(row)
	if not voucher_type or not voucher_no:
		return []

	available_fields = [
		fieldname for fieldname in requested_fields if _has_column("Stock Ledger Entry", fieldname)
	]
	if not available_fields:
		return []

	conditions = [
		"voucher_type = %(voucher_type)s",
		"voucher_no = %(voucher_no)s",
		"item_code = %(item_code)s",
		"actual_qty < 0",
	]
	params = {
		"voucher_type": voucher_type,
		"voucher_no": voucher_no,
		"item_code": row.get("item_code"),
	}
	if _has_column("Stock Ledger Entry", "is_cancelled"):
		conditions.append("IFNULL(is_cancelled, 0) = 0")
	if row.get("warehouse") and _has_column("Stock Ledger Entry", "warehouse"):
		conditions.append("warehouse = %(warehouse)s")
		params["warehouse"] = row.get("warehouse")

	if voucher_detail_no and _has_column("Stock Ledger Entry", "voucher_detail_no"):
		detail_rows = _query_outgoing_stock_ledger_rows(
			[*conditions, "voucher_detail_no = %(voucher_detail_no)s"],
			{**params, "voucher_detail_no": voucher_detail_no},
			available_fields,
		)
		return detail_rows

	return []


def _query_outgoing_stock_ledger_rows(conditions, params, fields):
	return frappe.db.sql(
		f"""
		SELECT {", ".join(fields)}
		FROM `tabStock Ledger Entry`
		WHERE {" AND ".join(conditions)}
		ORDER BY posting_date, posting_time, creation
		""",
		params,
		as_dict=True,
	)


def _get_outgoing_reference(row):
	if row.get("delivery_note"):
		return "Delivery Note", row.get("delivery_note"), row.get("dn_detail")

	if row.get("parenttype") in ("Delivery Note", "Sales Invoice"):
		return row.get("parenttype"), row.get("parent"), row.get("parent_detail_docname") or row.get("name")

	if row.get("sales_invoice"):
		return "Sales Invoice", row.get("sales_invoice"), row.get("sales_invoice_item")

	return None, None, None


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
	incomplete = sum(bool(d.get("cost_incomplete")) for d in parent_rows)

	return [
		{"value": sales_amount, "indicator": "Blue", "label": _("Sales Amount"), "datatype": "Currency"},
		{
			"value": cost_amount,
			"indicator": "Orange",
			"label": _("Known Bundle Cost (incomplete)") if incomplete else _("Bundle Cost"),
			"datatype": "Currency",
		},
		{
			"value": _("Incomplete costs") if incomplete else gross_profit,
			"indicator": "Orange" if incomplete else ("Green" if gross_profit >= 0 else "Red"),
			"label": _("Gross Profit"),
			"datatype": "Data" if incomplete else "Currency",
		},
		{
			"value": _("Incomplete costs") if incomplete else gross_profit_percent,
			"indicator": "Orange" if incomplete else ("Green" if gross_profit >= 0 else "Red"),
			"label": _("Gross Profit %"),
			"datatype": "Data" if incomplete else "Percent",
		},
		{
			"value": incomplete,
			"indicator": "Orange" if incomplete else "Green",
			"label": _("Rows requiring review"),
			"datatype": "Int",
		},
	]
