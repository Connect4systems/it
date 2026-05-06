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


def _has_column(doctype: str, column: str) -> bool:
    try:
        return bool(frappe.db.has_column(doctype, column))
    except Exception:
        return False


def _count_sql(query: str) -> int:
    return _i((frappe.db.sql(query) or [[0]])[0][0])


@frappe.whitelist()
def backfill_purchase_chain_sales_partner(dry_run: int = 1):
        """
        Backfill row-level Sales Partner in sequence:
          1) Material Request Item
          2) Purchase Order Item
          3) Purchase Receipt Item
          4) Purchase Invoice Item

        Dry run:
          bench --site <site> execute it.api.backfill_purchase_chain_sales_partner

        Apply:
          bench --site <site> execute it.api.backfill_purchase_chain_sales_partner --kwargs "{'dry_run': 0}"
        """
        dry_run = _i(dry_run)

        has_soi_sp = _has_column("Sales Order Item", "sales_partner")
        has_mri_sp = _has_column("Material Request Item", "sales_partner")
        has_poi_sp = _has_column("Purchase Order Item", "sales_partner")
        has_pri_sp = _has_column("Purchase Receipt Item", "sales_partner")
        has_pii_sp = _has_column("Purchase Invoice Item", "sales_partner")
        has_mr_header_sp = _has_column("Material Request", "custom_sales_partner")

        soi_partner = "NULLIF(soi.sales_partner, '')" if has_soi_sp else "NULL"
        mri_partner = "NULLIF(mri.sales_partner, '')" if has_mri_sp else "NULL"
        poi_partner = "NULLIF(poi.sales_partner, '')" if has_poi_sp else "NULL"
        pri_partner = "NULLIF(pri.sales_partner, '')" if has_pri_sp else "NULL"
        mr_header_partner = "NULLIF(mr.custom_sales_partner, '')" if has_mr_header_sp else "NULL"

        result = {
            "dry_run": bool(dry_run),
            "sequence": ["Material Request", "Purchase Order", "Purchase Receipt", "Purchase Invoice"],
            "counts": {
                "material_request_items_needing_update": 0,
                "material_request_items_updated": 0,
                "material_request_headers_updated": 0,
                "purchase_order_items_needing_update": 0,
                "purchase_order_items_updated": 0,
                "purchase_receipt_items_needing_update": 0,
                "purchase_receipt_items_updated": 0,
                "purchase_invoice_items_needing_update": 0,
                "purchase_invoice_items_updated": 0,
            },
            "skipped": [],
        }

        # 1) Material Request Item from SO item/header
        if not has_mri_sp:
            result["skipped"].append("Material Request Item.sales_partner column not found")
        else:
            mr_source = f"COALESCE({soi_partner}, NULLIF(so.sales_partner, ''))"
            mr_where = f"{mr_source} IS NOT NULL AND IFNULL(mri.sales_partner, '') != {mr_source}"

            mr_count_sql = f"""
                SELECT COUNT(*)
                FROM `tabMaterial Request Item` mri
                LEFT JOIN `tabSales Order Item` soi ON soi.name = mri.sales_order_item
                LEFT JOIN `tabSales Order` so
                    ON so.name = COALESCE(NULLIF(mri.sales_order, ''), soi.parent)
                WHERE mri.parenttype = 'Material Request'
                  AND {mr_where}
            """
            mr_needs = _count_sql(mr_count_sql)
            result["counts"]["material_request_items_needing_update"] = mr_needs

            if not dry_run and mr_needs:
                mr_update_sql = f"""
                    UPDATE `tabMaterial Request Item` mri
                    LEFT JOIN `tabSales Order Item` soi ON soi.name = mri.sales_order_item
                    LEFT JOIN `tabSales Order` so
                        ON so.name = COALESCE(NULLIF(mri.sales_order, ''), soi.parent)
                    SET mri.sales_partner = {mr_source}
                    WHERE mri.parenttype = 'Material Request'
                      AND {mr_where}
                """
                frappe.db.sql(mr_update_sql)
                result["counts"]["material_request_items_updated"] = mr_needs

            if has_mr_header_sp:
                mr_header_count_sql = """
                    SELECT COUNT(*)
                    FROM `tabMaterial Request` mr
                    INNER JOIN (
                        SELECT parent, MIN(sales_partner) AS partner, COUNT(DISTINCT sales_partner) AS cnt
                        FROM `tabMaterial Request Item`
                        WHERE IFNULL(sales_partner, '') != ''
                        GROUP BY parent
                    ) x ON x.parent = mr.name
                    WHERE x.cnt = 1
                      AND IFNULL(mr.custom_sales_partner, '') != x.partner
                """
                mr_header_needs = _count_sql(mr_header_count_sql)
                if not dry_run and mr_header_needs:
                    mr_header_update_sql = """
                        UPDATE `tabMaterial Request` mr
                        INNER JOIN (
                            SELECT parent, MIN(sales_partner) AS partner, COUNT(DISTINCT sales_partner) AS cnt
                            FROM `tabMaterial Request Item`
                            WHERE IFNULL(sales_partner, '') != ''
                            GROUP BY parent
                        ) x ON x.parent = mr.name
                        SET mr.custom_sales_partner = x.partner
                        WHERE x.cnt = 1
                          AND IFNULL(mr.custom_sales_partner, '') != x.partner
                    """
                    frappe.db.sql(mr_header_update_sql)
                    result["counts"]["material_request_headers_updated"] = mr_header_needs

        # 2) Purchase Order Item from SO item/header, then MR item/header
        if not has_poi_sp:
            result["skipped"].append("Purchase Order Item.sales_partner column not found")
        else:
            po_source = f"COALESCE({soi_partner}, NULLIF(so.sales_partner, ''), {mri_partner}, {mr_header_partner})"
            po_where = f"{po_source} IS NOT NULL AND IFNULL(poi.sales_partner, '') != {po_source}"

            po_count_sql = f"""
                SELECT COUNT(*)
                FROM `tabPurchase Order Item` poi
                LEFT JOIN `tabSales Order Item` soi ON soi.name = poi.sales_order_item
                LEFT JOIN `tabSales Order` so
                    ON so.name = COALESCE(NULLIF(poi.sales_order, ''), soi.parent)
                LEFT JOIN `tabMaterial Request Item` mri ON mri.name = poi.material_request_item
                LEFT JOIN `tabMaterial Request` mr
                    ON mr.name = COALESCE(NULLIF(poi.material_request, ''), mri.parent)
                WHERE poi.parenttype = 'Purchase Order'
                  AND {po_where}
            """
            po_needs = _count_sql(po_count_sql)
            result["counts"]["purchase_order_items_needing_update"] = po_needs

            if not dry_run and po_needs:
                po_update_sql = f"""
                    UPDATE `tabPurchase Order Item` poi
                    LEFT JOIN `tabSales Order Item` soi ON soi.name = poi.sales_order_item
                    LEFT JOIN `tabSales Order` so
                        ON so.name = COALESCE(NULLIF(poi.sales_order, ''), soi.parent)
                    LEFT JOIN `tabMaterial Request Item` mri ON mri.name = poi.material_request_item
                    LEFT JOIN `tabMaterial Request` mr
                        ON mr.name = COALESCE(NULLIF(poi.material_request, ''), mri.parent)
                    SET poi.sales_partner = {po_source}
                    WHERE poi.parenttype = 'Purchase Order'
                      AND {po_where}
                """
                frappe.db.sql(po_update_sql)
                result["counts"]["purchase_order_items_updated"] = po_needs

        # 3) Purchase Receipt Item from PO item, then SO, then MR
        if not has_pri_sp:
            result["skipped"].append("Purchase Receipt Item.sales_partner column not found")
        else:
            pr_source = (
                f"COALESCE({poi_partner}, {soi_partner}, NULLIF(so.sales_partner, ''), {mri_partner}, {mr_header_partner})"
            )
            pr_where = f"{pr_source} IS NOT NULL AND IFNULL(pri.sales_partner, '') != {pr_source}"

            pr_count_sql = f"""
                SELECT COUNT(*)
                FROM `tabPurchase Receipt Item` pri
                LEFT JOIN `tabPurchase Order Item` poi ON poi.name = pri.purchase_order_item
                LEFT JOIN `tabSales Order Item` soi ON soi.name = COALESCE(NULLIF(pri.sales_order_item, ''), poi.sales_order_item)
                LEFT JOIN `tabSales Order` so
                    ON so.name = COALESCE(NULLIF(pri.sales_order, ''), NULLIF(poi.sales_order, ''), soi.parent)
                LEFT JOIN `tabMaterial Request Item` mri
                    ON mri.name = COALESCE(NULLIF(pri.material_request_item, ''), poi.material_request_item)
                LEFT JOIN `tabMaterial Request` mr
                    ON mr.name = COALESCE(NULLIF(pri.material_request, ''), NULLIF(poi.material_request, ''), mri.parent)
                WHERE pri.parenttype = 'Purchase Receipt'
                  AND {pr_where}
            """
            pr_needs = _count_sql(pr_count_sql)
            result["counts"]["purchase_receipt_items_needing_update"] = pr_needs

            if not dry_run and pr_needs:
                pr_update_sql = f"""
                    UPDATE `tabPurchase Receipt Item` pri
                    LEFT JOIN `tabPurchase Order Item` poi ON poi.name = pri.purchase_order_item
                    LEFT JOIN `tabSales Order Item` soi ON soi.name = COALESCE(NULLIF(pri.sales_order_item, ''), poi.sales_order_item)
                    LEFT JOIN `tabSales Order` so
                        ON so.name = COALESCE(NULLIF(pri.sales_order, ''), NULLIF(poi.sales_order, ''), soi.parent)
                    LEFT JOIN `tabMaterial Request Item` mri
                        ON mri.name = COALESCE(NULLIF(pri.material_request_item, ''), poi.material_request_item)
                    LEFT JOIN `tabMaterial Request` mr
                        ON mr.name = COALESCE(NULLIF(pri.material_request, ''), NULLIF(poi.material_request, ''), mri.parent)
                    SET pri.sales_partner = {pr_source}
                    WHERE pri.parenttype = 'Purchase Receipt'
                      AND {pr_where}
                """
                frappe.db.sql(pr_update_sql)
                result["counts"]["purchase_receipt_items_updated"] = pr_needs

        # 4) Purchase Invoice Item from PR item, then PO item, then SO, then MR
        if not has_pii_sp:
            result["skipped"].append("Purchase Invoice Item.sales_partner column not found")
        else:
            pi_source = (
                f"COALESCE({pri_partner}, {poi_partner}, {soi_partner}, NULLIF(so.sales_partner, ''), {mri_partner}, {mr_header_partner})"
            )
            pi_where = f"{pi_source} IS NOT NULL AND IFNULL(pii.sales_partner, '') != {pi_source}"

            pi_count_sql = f"""
                SELECT COUNT(*)
                FROM `tabPurchase Invoice Item` pii
                LEFT JOIN `tabPurchase Receipt Item` pri ON pri.name = pii.pr_detail
                LEFT JOIN `tabPurchase Order Item` poi
                    ON poi.name = COALESCE(NULLIF(pii.po_detail, ''), pri.purchase_order_item)
                LEFT JOIN `tabSales Order Item` soi
                    ON soi.name = COALESCE(NULLIF(pii.sales_order_item, ''), pri.sales_order_item, poi.sales_order_item)
                LEFT JOIN `tabSales Order` so
                    ON so.name = COALESCE(NULLIF(pii.sales_order, ''), NULLIF(pri.sales_order, ''), NULLIF(poi.sales_order, ''), soi.parent)
                LEFT JOIN `tabMaterial Request Item` mri
                    ON mri.name = COALESCE(NULLIF(pii.material_request_item, ''), pri.material_request_item, poi.material_request_item)
                LEFT JOIN `tabMaterial Request` mr
                    ON mr.name = COALESCE(NULLIF(pii.material_request, ''), NULLIF(pri.material_request, ''), NULLIF(poi.material_request, ''), mri.parent)
                WHERE pii.parenttype = 'Purchase Invoice'
                  AND {pi_where}
            """
            pi_needs = _count_sql(pi_count_sql)
            result["counts"]["purchase_invoice_items_needing_update"] = pi_needs

            if not dry_run and pi_needs:
                pi_update_sql = f"""
                    UPDATE `tabPurchase Invoice Item` pii
                    LEFT JOIN `tabPurchase Receipt Item` pri ON pri.name = pii.pr_detail
                    LEFT JOIN `tabPurchase Order Item` poi
                        ON poi.name = COALESCE(NULLIF(pii.po_detail, ''), pri.purchase_order_item)
                    LEFT JOIN `tabSales Order Item` soi
                        ON soi.name = COALESCE(NULLIF(pii.sales_order_item, ''), pri.sales_order_item, poi.sales_order_item)
                    LEFT JOIN `tabSales Order` so
                        ON so.name = COALESCE(NULLIF(pii.sales_order, ''), NULLIF(pri.sales_order, ''), NULLIF(poi.sales_order, ''), soi.parent)
                    LEFT JOIN `tabMaterial Request Item` mri
                        ON mri.name = COALESCE(NULLIF(pii.material_request_item, ''), pri.material_request_item, poi.material_request_item)
                    LEFT JOIN `tabMaterial Request` mr
                        ON mr.name = COALESCE(NULLIF(pii.material_request, ''), NULLIF(pri.material_request, ''), NULLIF(poi.material_request, ''), mri.parent)
                    SET pii.sales_partner = {pi_source}
                    WHERE pii.parenttype = 'Purchase Invoice'
                      AND {pi_where}
                """
                frappe.db.sql(pi_update_sql)
                result["counts"]["purchase_invoice_items_updated"] = pi_needs

        if not dry_run:
            frappe.db.commit()

        return result
