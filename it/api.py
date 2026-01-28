# -*- coding: utf-8 -*-
from __future__ import annotations
import frappe  # MUST come before any @frappe.whitelist()

# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _f(v):
    try:
        return float(v or 0)
    except Exception:
        return 0.0


def _has(meta, fieldname: str) -> bool:
    try:
        return bool(meta.get_field(fieldname))
    except Exception:
        return False


def _item_name(item_code: str) -> str:
    if not item_code:
        return ""
    return frappe.db.get_value("Item", item_code, "item_name") or item_code


def _delivery_bom_rows_from_opportunity(opp) -> list[dict]:
    rows: list[dict] = []
    for r in (opp.get("custom_product_bundle") or []):
        item_code = r.get("item_code")
        if not item_code:
            continue
        rows.append(
            {
                "item": item_code,
                "item_name": _item_name(item_code),
                "description": r.get("description") or "",
                "qty": _f(r.get("qty")),
            }
        )
    return rows


def _delivery_bom_rows_from_doc(doc) -> list[dict]:
    rows: list[dict] = []
    for r in (doc.get("custom_delivery_bom") or []):
        item_code = r.get("item") or r.get("item_code")
        if not item_code:
            continue
        row = {
            "item": item_code,
            "item_name": r.get("item_name") or _item_name(item_code),
            "description": r.get("description") or "",
            "qty": _f(r.get("qty")),
        }
        if hasattr(r, "custom_parent_product"):
            row["custom_parent_product"] = r.get("custom_parent_product")
        rows.append(row)
    return rows


# ----------------------------------------------------------------------
# 1) Opportunity -> Quotation   (copy Product Bundle -> custom_delivery_bom)
# ----------------------------------------------------------------------
@frappe.whitelist()
def make_quotation_with_bundle(source_name: str, target_doc: dict | None = None):
    """
    Call ERPNext core Opportunity->Quotation mapping, then also copy:
      Opportunity.custom_product_bundle  -->  Quotation.custom_delivery_bom
    """
    from erpnext.crm.doctype.opportunity.opportunity import (
        make_quotation as core_make_quotation,
    )

    qtn = core_make_quotation(source_name, target_doc)

    try:
        opp = frappe.get_doc("Opportunity", source_name)
    except frappe.DoesNotExistError:
        return qtn

    # ensure child exists, then (re)fill
    if not hasattr(qtn, "custom_delivery_bom"):
        return qtn

    qtn.custom_delivery_bom = []
    for r in (opp.get("custom_product_bundle") or []):
        item_code = r.get("item_code")
        if not item_code:
            continue
        row = qtn.append("custom_delivery_bom", {})
        row.item        = item_code
        row.item_name   = frappe.db.get_value("Item", item_code, "item_name") or item_code
        row.description = r.get("description") or ""
        row.qty         = _f(r.get("qty"))

    qtn.flags.ignore_permissions = True
    try:
        qtn.run_method("set_missing_values")
        qtn.run_method("calculate_taxes_and_totals")
    except Exception:
        pass

    return qtn


# ----------------------------------------------------------------------
# 2) Quotation -> Sales Order   (carry custom_delivery_bom forward)
# ----------------------------------------------------------------------
@frappe.whitelist()
def make_sales_order_with_bundle(source_name: str, target_doc: dict | None = None):
    """
    Call ERPNext core Quotation->Sales Order mapping, then copy:
      Quotation.custom_delivery_bom  -->  Sales Order.custom_delivery_bom
    """
    from erpnext.selling.doctype.quotation.quotation import (
        make_sales_order as core_make_sales_order,
    )

    so = core_make_sales_order(source_name, target_doc)

    try:
        qtn = frappe.get_doc("Quotation", source_name)
    except frappe.DoesNotExistError:
        return so

    if not hasattr(so, "custom_delivery_bom"):
        return so

    so.custom_delivery_bom = []
    for r in (qtn.get("custom_delivery_bom") or []):
        item_code = r.get("item")
        if not item_code:
            continue
        row = so.append("custom_delivery_bom", {})
        row.item        = item_code
        row.item_name   = r.get("item_name") or frappe.db.get_value("Item", item_code, "item_name") or item_code
        row.description = r.get("description") or ""
        row.qty         = _f(r.get("qty"))

    so.flags.ignore_permissions = True
    try:
        so.run_method("set_missing_values")
        so.run_method("calculate_taxes_and_totals")
    except Exception:
        pass

    return so


# ----------------------------------------------------------------------
# 3) Sales Order -> Delivery Note   (append components, no SO link, rate=0)
# ----------------------------------------------------------------------
@frappe.whitelist()
def make_delivery_note_merged(source_name: str, target_doc: dict | None = None):
    """
    1) Use ERPNext core mapper for SO Items (keeps so_detail links).
    2) Append extra rows from Sales Order.custom_delivery_bom without SO linkage,
       and with rate = 0 (parent SO item carries the price).
    """
    from erpnext.selling.doctype.sales_order.sales_order import (
        make_delivery_note as core_make_delivery_note,
    )

    dn = core_make_delivery_note(source_name, target_doc)

    try:
        so = frappe.get_doc("Sales Order", source_name)
    except frappe.DoesNotExistError:
        return dn

    for r in (so.get("custom_delivery_bom") or []):
        item_code = r.get("item")
        if not item_code:
            continue

        dnr = dn.append("items", {})
        dnr.item_code   = item_code
        dnr.item_name   = r.get("item_name") or frappe.db.get_value("Item", item_code, "item_name") or item_code
        dnr.description = r.get("description") or ""
        dnr.uom         = frappe.db.get_value("Item", item_code, "stock_uom")
        dnr.qty         = _f(r.get("qty"))

        # DO NOT set sales order links on these component rows
        # dnr.against_sales_order = None
        # dnr.sales_order = None
        # dnr.so_detail = None

        dnr.rate = 0
        dnr.discount_percentage = 0
        dnr.discount_amount = 0

    dn.flags.ignore_permissions = True
    try:
        dn.run_method("set_missing_values")
        dn.run_method("calculate_taxes_and_totals")
    except Exception:
        pass

    return dn


# ----------------------------------------------------------------------
# 4) Sales Order -> Sales Invoice   (append bundle rows, rate=0)
# ----------------------------------------------------------------------
@frappe.whitelist()
def make_sales_invoice_merged(source_name: str, target_doc: dict | None = None):
    """
    1) Use ERPNext core mapper for SO Items.
    2) Append extra rows from Sales Order.custom_delivery_bom with rate = 0.
       Skip if the row already exists (e.g., SI created from DN).
    """
    from erpnext.selling.doctype.sales_order.sales_order import (
        make_sales_invoice as core_make_sales_invoice,
    )

    si = core_make_sales_invoice(source_name, target_doc)

    try:
        so = frappe.get_doc("Sales Order", source_name)
    except frappe.DoesNotExistError:
        return si

    existing = {
        (d.item_code, _f(d.qty), (d.description or "").strip())
        for d in (si.items or [])
        if d.item_code
    }

    for r in (so.get("custom_delivery_bom") or []):
        item_code = r.get("item")
        if not item_code:
            continue

        key = (item_code, _f(r.get("qty")), (r.get("description") or "").strip())
        if key in existing:
            continue

        sir = si.append("items", {})
        sir.item_code   = item_code
        sir.item_name   = r.get("item_name") or _item_name(item_code)
        sir.description = r.get("description") or ""
        sir.uom         = frappe.db.get_value("Item", item_code, "stock_uom")
        sir.qty         = _f(r.get("qty"))
        sir.rate = 0
        sir.discount_percentage = 0
        sir.discount_amount = 0

    si.flags.ignore_permissions = True
    try:
        si.run_method("set_missing_values")
        si.run_method("calculate_taxes_and_totals")
    except Exception:
        pass

    return si


# ----------------------------------------------------------------------
# 5) Utility APIs used by client scripts
# ----------------------------------------------------------------------
@frappe.whitelist()
def get_delivery_bom_from_opportunity_bundle(opportunity_name: str):
    try:
        opp = frappe.get_doc("Opportunity", opportunity_name)
    except frappe.DoesNotExistError:
        return []
    return _delivery_bom_rows_from_opportunity(opp)


@frappe.whitelist()
def get_delivery_bom_from_quotation(quotation_name: str):
    try:
        qtn = frappe.get_doc("Quotation", quotation_name)
    except frappe.DoesNotExistError:
        return []
    return _delivery_bom_rows_from_doc(qtn)


@frappe.whitelist()
def get_delivery_bom_from_sales_order(sales_order_name: str):
    try:
        so = frappe.get_doc("Sales Order", sales_order_name)
    except frappe.DoesNotExistError:
        return []
    return _delivery_bom_rows_from_doc(so)


# ----------------------------------------------------------------------
# 6) Sales Order -> Purchase Order   (items + custom_delivery_bom)
# ----------------------------------------------------------------------
@frappe.whitelist()
def make_purchase_order_from_so_bundle(sales_order: str):
    so = frappe.get_doc("Sales Order", sales_order)

    po = frappe.new_doc("Purchase Order")
    po.company = so.company
    po.currency = so.currency

    schedule_date = getattr(so, "delivery_date", None) or frappe.utils.today()

    po_item_meta = frappe.get_meta("Purchase Order Item")

    def add_po_item(item_code: str, item_name: str, description: str, qty: float, uom: str | None = None,
                    conversion_factor: float | None = None, sales_order_item: str | None = None):
        if not item_code or qty <= 0:
            return

        row = po.append("items", {})
        row.item_code = item_code
        row.item_name = item_name or _item_name(item_code)
        row.description = description or ""
        row.qty = _f(qty)
        row.uom = uom or frappe.db.get_value("Item", item_code, "stock_uom")
        row.conversion_factor = conversion_factor or 1
        row.schedule_date = schedule_date
        row.rate = 0

        if _has(po_item_meta, "sales_order"):
            row.sales_order = so.name
        if sales_order_item and _has(po_item_meta, "sales_order_item"):
            row.sales_order_item = sales_order_item

    # 1) Add Sales Order items (parent/main rows)
    for it in (so.items or []):
        add_po_item(
            item_code=it.item_code,
            item_name=it.item_name,
            description=it.description,
            qty=_f(it.qty),
            uom=it.uom,
            conversion_factor=_f(getattr(it, "conversion_factor", 1)),
            sales_order_item=it.name,
        )

    # 2) Add custom_delivery_bom rows (components)
    for r in (so.get("custom_delivery_bom") or []):
        item_code = r.get("item")
        if not item_code:
            continue

        desc = r.get("description") or ""
        if hasattr(r, "custom_parent_product") and r.get("custom_parent_product"):
            desc = f"{desc}\nComponent of {r.get('custom_parent_product')}" if desc else f"Component of {r.get('custom_parent_product')}"

        add_po_item(
            item_code=item_code,
            item_name=r.get("item_name"),
            description=desc,
            qty=_f(r.get("qty")),
            uom=None,
            conversion_factor=1,
            sales_order_item=None,
        )

    po.flags.ignore_permissions = True
    try:
        po.run_method("set_missing_values")
        po.run_method("calculate_taxes_and_totals")
    except Exception:
        pass

    return po
