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
