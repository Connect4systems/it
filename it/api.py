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


def _i(v):
    try:
        return int(v or 0)
    except Exception:
        return 0


def _has(meta, fieldname: str) -> bool:
    try:
        return bool(meta.get_field(fieldname))
    except Exception:
        return False


def _item_name(item_code: str) -> str:
    if not item_code:
        return ""
    return frappe.db.get_value("Item", item_code, "item_name") or item_code


def _upsert_standard_product_bundle(parent_item_code: str, components: list[dict]) -> str | None:
    """
    Ensure there is a standard Product Bundle for `parent_item_code` and
    replace its component table with the given rows.
    """
    if not parent_item_code:
        return None

    existing_name = frappe.db.get_value("Product Bundle", {"new_item_code": parent_item_code}, "name")
    if existing_name:
        pb = frappe.get_doc("Product Bundle", existing_name)
    else:
        pb = frappe.new_doc("Product Bundle")
        pb.new_item_code = parent_item_code

    pb.items = []
    for comp in components:
        item_code = comp.get("item_code")
        qty = _f(comp.get("qty"))
        if not item_code or qty <= 0:
            continue

        row = pb.append("items", {})
        row.item_code = item_code
        row.qty = qty
        if hasattr(row, "description"):
            row.description = comp.get("description") or ""
        if hasattr(row, "uom"):
            row.uom = comp.get("uom") or frappe.db.get_value("Item", item_code, "stock_uom")

    pb.flags.ignore_permissions = True
    if existing_name:
        pb.save()
    else:
        pb.insert()

    return pb.name


def _delivery_bom_rows_from_opportunity(opp) -> list[dict]:
    rows: list[dict] = []
    for r in (opp.get("custom_product_bundle") or []):
        item_code = r.get("item_code")
        if not item_code:
            continue
        total_qty = r.get("custom_total_qty")
        qty = _f(total_qty) if total_qty is not None else _f(r.get("qty"))
        uom = r.get("uom") or frappe.db.get_value("Item", item_code, "stock_uom")
        conversion_factor = _f(r.get("conversion_factor") or 1)
        parent_product = r.get("custom_product") or r.get("custom_parent_product")
        rows.append(
            {
                "item": item_code,
                "item_name": _item_name(item_code),
                "description": r.get("description") or "",
                "qty": qty,
                "uom": uom,
                "conversion_factor": conversion_factor,
                "custom_parent_product": parent_product,
            }
        )
    return rows


def _delivery_bom_rows_from_doc(doc) -> list[dict]:
    rows: list[dict] = []
    for r in (doc.get("custom_delivery_bom") or []):
        item_code = r.get("item") or r.get("item_code")
        if not item_code:
            continue
        uom = r.get("uom") or frappe.db.get_value("Item", item_code, "stock_uom")
        conversion_factor = _f(r.get("conversion_factor") or 1)
        row = {
            "item": item_code,
            "item_name": r.get("item_name") or _item_name(item_code),
            "description": r.get("description") or "",
            "qty": _f(r.get("qty")),
            "uom": uom,
            "conversion_factor": conversion_factor,
        }
        if hasattr(r, "custom_parent_product"):
            row["custom_parent_product"] = r.get("custom_parent_product")
        rows.append(row)
    return rows


# ----------------------------------------------------------------------
# 1) Opportunity -> Quotation   (prepare standard Product Bundle for MAIN items)
# ----------------------------------------------------------------------
@frappe.whitelist()
def make_quotation_with_bundle(source_name: str, target_doc: dict | None = None):
    """
    Call ERPNext core Opportunity->Quotation mapping, then ensure each MAIN
    Opportunity Item has a standard Product Bundle built from
    Opportunity.custom_product_bundle rows linked by custom_product.
    """
    from erpnext.crm.doctype.opportunity.opportunity import (
        make_quotation as core_make_quotation,
    )

    qtn = core_make_quotation(source_name, target_doc)

    try:
        opp = frappe.get_doc("Opportunity", source_name)
    except frappe.DoesNotExistError:
        return qtn

    components_by_parent: dict[str, list[dict]] = {}
    for r in (opp.get("custom_product_bundle") or []):
        parent = r.get("custom_product") or r.get("custom_parent_product")
        item_code = r.get("item_code")
        qty = _f(r.get("qty"))  # per-one qty for standard Product Bundle master
        if not parent or not item_code or qty <= 0:
            continue

        components_by_parent.setdefault(parent, []).append(
            {
                "item_code": item_code,
                "qty": qty,
                "description": r.get("description") or "",
                "uom": r.get("uom"),
            }
        )

    for it in (opp.get("items") or []):
        parent_item = it.get("item_code")
        if not parent_item or not _i(it.get("custom_main")):
            continue
        _upsert_standard_product_bundle(parent_item, components_by_parent.get(parent_item, []))

    qtn.flags.ignore_permissions = True
    try:
        # Make sure standard packed items can be rebuilt from Product Bundle.
        if hasattr(qtn, "set_packed_items"):
            qtn.set_packed_items()
        qtn.run_method("set_missing_values")
        qtn.run_method("calculate_taxes_and_totals")
    except Exception:
        pass

    return qtn


# ----------------------------------------------------------------------
# 2) Quotation -> Sales Order   (standard ERPNext flow)
# ----------------------------------------------------------------------
@frappe.whitelist()
def make_sales_order_with_bundle(source_name: str, target_doc: dict | None = None):
    """Use ERPNext standard Quotation -> Sales Order mapping."""
    from erpnext.selling.doctype.quotation.quotation import (
        make_sales_order as core_make_sales_order,
    )
    return core_make_sales_order(source_name, target_doc)


# ----------------------------------------------------------------------
# 3) Sales Order -> Delivery Note   (standard ERPNext flow)
# ----------------------------------------------------------------------
@frappe.whitelist()
def make_delivery_note_merged(source_name: str, target_doc: dict | None = None):
    """Use ERPNext standard Sales Order -> Delivery Note mapping."""
    from erpnext.selling.doctype.sales_order.sales_order import (
        make_delivery_note as core_make_delivery_note,
    )
    return core_make_delivery_note(source_name, target_doc)


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
        sir.uom         = r.get("uom") or frappe.db.get_value("Item", item_code, "stock_uom")
        if hasattr(sir, "conversion_factor"):
            sir.conversion_factor = _f(r.get("conversion_factor") or 1)
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


# ----------------------------------------------------------------------
# 7) Sales Order -> Purchase Order override (include custom_delivery_bom)
# ----------------------------------------------------------------------
@frappe.whitelist()
def make_purchase_order_merged(source_name: str, target_doc: dict | None = None):
    # ignore target_doc to avoid core checks and ensure BOM items are included
    return make_purchase_order_from_so_bundle(source_name)


@frappe.whitelist()
def make_purchase_order_from_so_selection(sales_order: str, selections):
    """
    Create a Purchase Order from a Sales Order using a custom selection list
    that can include both SO items and custom_delivery_bom rows.

    selections: list of dicts with keys:
      - source: "SO" | "BOM"
      - item_code
      - item_name
      - description
      - qty
      - uom (optional)
      - conversion_factor (optional)
      - sales_order_item (optional, for SO rows)
    """
    selections = frappe.parse_json(selections)
    if not isinstance(selections, list):
        return None

    so = frappe.get_doc("Sales Order", sales_order)

    po = frappe.new_doc("Purchase Order")
    po.company = so.company
    po.currency = so.currency

    schedule_date = getattr(so, "delivery_date", None) or frappe.utils.today()
    po_item_meta = frappe.get_meta("Purchase Order Item")

    def add_po_item(row: dict):
        item_code = row.get("item_code")
        qty = _f(row.get("qty"))
        if not item_code or qty <= 0:
            return

        d = po.append("items", {})
        d.item_code = item_code
        d.item_name = row.get("item_name") or _item_name(item_code)
        d.description = row.get("description") or ""
        d.qty = qty
        d.uom = row.get("uom") or frappe.db.get_value("Item", item_code, "stock_uom")
        d.conversion_factor = _f(row.get("conversion_factor") or 1)
        d.schedule_date = schedule_date
        d.rate = 0

        if _has(po_item_meta, "sales_order"):
            d.sales_order = so.name
        so_item = row.get("sales_order_item")
        if so_item and _has(po_item_meta, "sales_order_item"):
            d.sales_order_item = so_item

    for row in selections:
        if isinstance(row, dict):
            add_po_item(row)

    po.flags.ignore_permissions = True
    try:
        po.run_method("set_missing_values")
        po.run_method("calculate_taxes_and_totals")
    except Exception:
        pass

    return po


# ----------------------------------------------------------------------
# 8) Sales Order -> Purchase Order item picker (include custom_delivery_bom)
# ----------------------------------------------------------------------
@frappe.whitelist()
def get_items_merged(source_name: str, target_doc: dict | None = None):
    """
    Extend ERPNext Sales Order -> Purchase Order item picker to include
    Sales Order.custom_delivery_bom rows.
    """
    from erpnext.selling.doctype.sales_order.sales_order import (
        get_items as core_get_items,
    )

    base = core_get_items(source_name, target_doc)

    # normalize to list
    if isinstance(base, dict) and "items" in base:
        items_list = base.get("items") or []
        container = base
    else:
        items_list = base if isinstance(base, list) else []
        container = None

    try:
        so = frappe.get_doc("Sales Order", source_name)
    except frappe.DoesNotExistError:
        return base

    schedule_date = getattr(so, "delivery_date", None) or frappe.utils.today()

    for r in (so.get("custom_delivery_bom") or []):
        item_code = r.get("item")
        if not item_code:
            continue

        qty = _f(r.get("qty"))
        row = {
            "item_code": item_code,
            "item_name": r.get("item_name") or _item_name(item_code),
            "description": r.get("description") or "",
            "qty": qty,
            "pending_qty": qty,
            "uom": r.get("uom") or frappe.db.get_value("Item", item_code, "stock_uom"),
            "stock_uom": frappe.db.get_value("Item", item_code, "stock_uom"),
            "conversion_factor": _f(r.get("conversion_factor") or 1),
            "schedule_date": schedule_date,
            "supplier": None,
            "sales_order_item": None,
        }
        items_list.append(row)

    if container is not None:
        container["items"] = items_list
        return container

    return items_list


@frappe.whitelist()
def get_items_from_sales_order_merged(sales_order: str, *args, **kwargs):
    """
    Override Purchase Order item picker to include Sales Order.custom_delivery_bom rows.
    Compatible with different ERPNext versions by accepting *args/**kwargs.
    """
    try:
        from erpnext.buying.doctype.purchase_order.purchase_order import (
            get_items_from_sales_order as core_get_items_from_sales_order,
        )
    except Exception:
        return []

    base = core_get_items_from_sales_order(sales_order, *args, **kwargs)

    # normalize to list
    if isinstance(base, dict) and "items" in base:
        items_list = base.get("items") or []
        container = base
    else:
        items_list = base if isinstance(base, list) else []
        container = None

    try:
        so = frappe.get_doc("Sales Order", sales_order)
    except frappe.DoesNotExistError:
        return base

    schedule_date = getattr(so, "delivery_date", None) or frappe.utils.today()

    for r in (so.get("custom_delivery_bom") or []):
        item_code = r.get("item")
        if not item_code:
            continue

        qty = _f(r.get("qty"))
        row = {
            "item_code": item_code,
            "item_name": r.get("item_name") or _item_name(item_code),
            "description": r.get("description") or "",
            "qty": qty,
            "pending_qty": qty,
            "uom": r.get("uom") or frappe.db.get_value("Item", item_code, "stock_uom"),
            "stock_uom": frappe.db.get_value("Item", item_code, "stock_uom"),
            "conversion_factor": _f(r.get("conversion_factor") or 1),
            "schedule_date": schedule_date,
            "supplier": None,
            "sales_order": so.name,
            "sales_order_item": None,
        }
        items_list.append(row)

    if container is not None:
        container["items"] = items_list
        return container

    return items_list


# ----------------------------------------------------------------------
# 9) Bench utility: backfill Sales Partner on Sales Invoice Items
# ----------------------------------------------------------------------
def backfill_sales_invoice_item_sales_partner(dry_run: int = 1):
        """
        Sync all Sales Invoice Item.sales_partner from parent Sales Invoice.sales_partner.

        Run (preview only):
            bench --site <site> execute it.api.backfill_sales_invoice_item_sales_partner

        Run (apply updates):
            bench --site <site> execute it.api.backfill_sales_invoice_item_sales_partner --kwargs "{'dry_run': 0}"
        """
        dry_run = _i(dry_run)

        to_update = frappe.db.sql(
                """
                SELECT COUNT(*)
                FROM `tabSales Invoice Item` sii
                INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
                WHERE sii.parenttype = 'Sales Invoice'
                    AND IFNULL(sii.sales_partner, '') != IFNULL(si.sales_partner, '')
                """
        )[0][0]

        result = {
                "dry_run": bool(dry_run),
                "rows_needing_update": _i(to_update),
                "rows_updated": 0,
        }

        if dry_run or not to_update:
                return result

        frappe.db.sql(
                """
                UPDATE `tabSales Invoice Item` sii
                INNER JOIN `tabSales Invoice` si ON si.name = sii.parent
                SET sii.sales_partner = si.sales_partner
                WHERE sii.parenttype = 'Sales Invoice'
                    AND IFNULL(sii.sales_partner, '') != IFNULL(si.sales_partner, '')
                """
        )
        frappe.db.commit()

        result["rows_updated"] = _i(to_update)
        return result
