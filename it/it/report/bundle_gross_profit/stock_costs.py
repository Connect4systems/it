"""Explicit invoice/delivery allocation and signed stock costs for Bundle Gross Profit."""

import frappe
from frappe import _
from frappe.utils import flt


def stock_qty(row):
	value = row.get("stock_qty")
	return flt(row.get("qty") if value is None else value)


def invoice_sources(item):
	"""Never infer a delivery from a shared Sales Order or a return's original issue."""
	if item.get("update_stock"):
		source = frappe._dict(item)
		source.update(allocation_ratio=1, delivery_note=None, dn_detail=None)
		return [source], None

	params = {
		"company": item.company,
		"invoice": item.sales_invoice,
		"detail": item.sales_invoice_item,
		"item_code": item.item_code,
		"delivery_note": item.get("delivery_note"),
		"dn_detail": item.get("dn_detail"),
		"qty": stock_qty(item),
	}
	# si_detail identifies the invoice row even when an invoice precedes several deliveries.
	conditions = ["dni.si_detail = %(detail)s"]
	if item.get("delivery_note") and item.get("dn_detail"):
		conditions.append("(dni.parent = %(delivery_note)s AND dni.name = %(dn_detail)s)")
	elif item.get("delivery_note"):
		conditions.append("dni.parent = %(delivery_note)s")
	conditions.append("""(dni.against_sales_invoice = %(invoice)s
		AND IFNULL(dni.si_detail, '') = ''
		AND (SELECT COUNT(*) FROM `tabSales Invoice Item` sibling
			WHERE sibling.parent = %(invoice)s AND sibling.item_code = %(item_code)s) = 1)""")
	deliveries = frappe.db.sql(
		f"""SELECT dni.name, dni.parent, dni.qty, dni.stock_qty, dni.warehouse,
			dni.si_detail, dni.against_sales_invoice, dn.posting_date
		FROM `tabDelivery Note Item` dni
		INNER JOIN `tabDelivery Note` dn ON dn.name = dni.parent
		WHERE dn.docstatus = 1 AND dn.company = %(company)s
			AND dni.item_code = %(item_code)s AND dni.stock_qty * %(qty)s > 0
			AND ({" OR ".join(conditions)})
		ORDER BY dn.posting_date, dn.posting_time, dn.name, dni.idx""",
		params,
		as_dict=True,
	)
	if not deliveries:
		return [], _("No explicitly linked stock movement; check delivery/return links")
	if not item.get("dn_detail") and item.get("delivery_note"):
		ambiguous = [
			d for d in deliveries if d.parent == item.delivery_note and d.si_detail != item.sales_invoice_item
		]
		if len(ambiguous) > 1:
			return [], _("Multiple delivery rows match; set the invoice Delivery Note Item link")

	# All-time demand makes this validation independent of date/customer/report filters.
	for delivery in deliveries:
		demand = frappe.db.sql(
			"""SELECT SUM(ABS(sii.stock_qty)) AS qty,
				SUM(CASE WHEN (%(reverse_detail)s != '' AND sii.name != %(reverse_detail)s)
					OR (%(reverse_detail)s = '' AND %(reverse_invoice)s != '' AND si.name != %(reverse_invoice)s)
					THEN 1 ELSE 0 END) AS conflicting_links
			FROM `tabSales Invoice Item` sii
			INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
			WHERE si.docstatus = 1 AND si.company = %(company)s AND si.update_stock = 0
				AND sii.item_code = %(item_code)s AND sii.stock_qty * %(qty)s > 0
				AND (sii.dn_detail = %(delivery_detail)s
					OR (sii.delivery_note = %(delivery)s AND IFNULL(sii.dn_detail, '') = ''))""",
			{
				**params,
				"delivery_detail": delivery.name,
				"delivery": delivery.parent,
				"reverse_detail": delivery.get("si_detail") or "",
				"reverse_invoice": delivery.get("against_sales_invoice") or "",
			},
			as_dict=True,
		)
		if demand and flt(demand[0].get("conflicting_links")):
			return [], _("Delivery and invoice links conflict; review the linked invoice items")
		if flt(demand[0].qty if demand else 0) > abs(stock_qty(delivery)) + 0.000001:
			return [], _("Linked invoice quantities exceed delivery quantity; review duplicate/partial links")

	total_delivered = sum(abs(stock_qty(d)) for d in deliveries)
	requested = abs(stock_qty(item))
	if len(deliveries) > 1 and total_delivered > requested + 0.000001:
		return [], _("Linked deliveries exceed invoice quantity; allocation needs review")
	sources = []
	remaining = requested
	for delivery in deliveries:
		allocated = min(remaining, abs(stock_qty(delivery)))
		if not allocated:
			continue
		sign = -1 if stock_qty(item) < 0 else 1
		source = frappe._dict(item)
		source.update(
			delivery_note=delivery.parent,
			dn_detail=delivery.name,
			warehouse=delivery.warehouse,
			stock_qty=sign * allocated,
			allocation_ratio=allocated / abs(stock_qty(delivery)),
		)
		sources.append(source)
		remaining -= allocated
	return sources, (
		_("Partially delivered; remaining quantity has no linked stock cost")
		if remaining > 0.000001
		else None
	)


def ledger_cost(row):
	"""Cost is negative stock value change, including inward returns and genuine zeros."""
	voucher_type = row.get("parenttype")
	voucher_no = row.get("parent")
	detail = row.get("parent_detail_docname") or row.get("name")
	if not voucher_type:
		if row.get("update_stock"):
			voucher_type, voucher_no, detail = (
				"Sales Invoice",
				row.get("sales_invoice"),
				row.get("sales_invoice_item"),
			)
		else:
			voucher_type, voucher_no, detail = "Delivery Note", row.get("delivery_note"), row.get("dn_detail")
	if not voucher_no or not detail:
		return None, None, _("Exact stock voucher item link unavailable")
	entries = frappe.db.sql(
		"""SELECT actual_qty, stock_value_difference
		FROM `tabStock Ledger Entry`
		WHERE company = %(company)s AND voucher_type = %(voucher_type)s
			AND voucher_no = %(voucher_no)s AND voucher_detail_no = %(detail)s
			AND item_code = %(item_code)s AND IFNULL(is_cancelled, 0) = 0
		ORDER BY posting_date, posting_time, creation""",
		{
			"company": row.get("company"),
			"voucher_type": voucher_type,
			"voucher_no": voucher_no,
			"detail": detail,
			"item_code": row.get("item_code"),
		},
		as_dict=True,
	)
	if not entries or any(e.stock_value_difference is None for e in entries):
		return None, None, _("Exact stock ledger value unavailable")
	# Include both sides of internal transfers: their net inventory cost is zero.
	net_qty = -sum(flt(e.actual_qty) for e in entries)
	value = -sum(flt(e.stock_value_difference) for e in entries)
	qty = stock_qty(row)
	if not net_qty:
		if not value:
			return 0, 0, _("Stock Ledger: no net stock cost")
		return None, None, _("Stock value adjustment without quantity; review required")
	if net_qty * qty <= 0:
		return None, None, _("Stock movement direction does not match invoice/return")
	if abs(qty) > abs(net_qty) + 0.000001:
		return None, None, _("Invoice/component quantity exceeds exact stock movement")
	rate = value / net_qty
	return rate, qty * rate, _("Actual Stock Ledger value")
