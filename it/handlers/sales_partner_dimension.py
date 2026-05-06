from __future__ import annotations

import frappe


def _as_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _has_field(doctype: str, fieldname: str, cache: dict[tuple[str, str], bool]) -> bool:
    key = (doctype, fieldname)
    if key in cache:
        return cache[key]
    try:
        cache[key] = bool(frappe.get_meta(doctype).get_field(fieldname))
    except Exception:
        cache[key] = False
    return cache[key]


def _db_get_value(doctype: str, name: str, fields, as_dict: bool = True):
    if not name:
        return None
    try:
        return frappe.db.get_value(doctype, name, fields, as_dict=as_dict)
    except Exception:
        return None


def _set_row_sales_partner(row, partner: str | None) -> bool:
    if not hasattr(row, "sales_partner"):
        return False
    current = _as_text(getattr(row, "sales_partner", None))
    target = _as_text(partner)
    if current == target:
        return False
    row.sales_partner = target
    return True


def _get_so_partner(so_name: str, cache: dict) -> str | None:
    so_name = _as_text(so_name)
    if not so_name:
        return None
    if so_name not in cache["so_partner"]:
        cache["so_partner"][so_name] = _as_text(
            _db_get_value("Sales Order", so_name, "sales_partner", as_dict=False)
        )
    return cache["so_partner"].get(so_name)


def _get_mr_partner(mr_name: str, cache: dict) -> str | None:
    mr_name = _as_text(mr_name)
    if not mr_name:
        return None
    if mr_name not in cache["mr_partner"]:
        value = _db_get_value("Material Request", mr_name, ["custom_sales_partner"], as_dict=True)
        cache["mr_partner"][mr_name] = _as_text((value or {}).get("custom_sales_partner"))
    return cache["mr_partner"].get(mr_name)


def _get_so_item_partner(so_item: str, cache: dict) -> str | None:
    so_item = _as_text(so_item)
    if not so_item:
        return None
    if so_item in cache["so_item_partner"]:
        return cache["so_item_partner"][so_item]

    if _has_field("Sales Order Item", "sales_partner", cache["field_exists"]):
        row = _db_get_value("Sales Order Item", so_item, ["sales_partner", "parent"], as_dict=True) or {}
    else:
        row = _db_get_value("Sales Order Item", so_item, ["parent"], as_dict=True) or {}

    partner = _as_text(row.get("sales_partner"))
    if not partner:
        partner = _get_so_partner(row.get("parent"), cache)

    cache["so_item_partner"][so_item] = partner
    return partner


def _get_mr_item_partner(mr_item: str, cache: dict) -> str | None:
    mr_item = _as_text(mr_item)
    if not mr_item:
        return None
    if mr_item in cache["mr_item_partner"]:
        return cache["mr_item_partner"][mr_item]

    if _has_field("Material Request Item", "sales_partner", cache["field_exists"]):
        row = _db_get_value("Material Request Item", mr_item, ["sales_partner", "parent"], as_dict=True) or {}
    else:
        row = _db_get_value("Material Request Item", mr_item, ["parent"], as_dict=True) or {}

    partner = _as_text(row.get("sales_partner"))
    if not partner:
        partner = _get_mr_partner(row.get("parent"), cache)

    cache["mr_item_partner"][mr_item] = partner
    return partner


def _get_po_item_partner(po_item: str, cache: dict) -> str | None:
    po_item = _as_text(po_item)
    if not po_item:
        return None
    if po_item in cache["po_item_partner"]:
        return cache["po_item_partner"][po_item]

    fields = ["parent", "sales_order", "sales_order_item", "material_request", "material_request_item"]
    if _has_field("Purchase Order Item", "sales_partner", cache["field_exists"]):
        fields = ["sales_partner"] + fields

    row = _db_get_value("Purchase Order Item", po_item, fields, as_dict=True) or {}

    partner = _as_text(row.get("sales_partner"))
    if not partner:
        partner = (
            _get_so_item_partner(row.get("sales_order_item"), cache)
            or _get_so_partner(row.get("sales_order"), cache)
            or _get_mr_item_partner(row.get("material_request_item"), cache)
            or _get_mr_partner(row.get("material_request"), cache)
        )

    cache["po_item_partner"][po_item] = partner
    return partner


def _get_pr_item_partner(pr_item: str, cache: dict) -> str | None:
    pr_item = _as_text(pr_item)
    if not pr_item:
        return None
    if pr_item in cache["pr_item_partner"]:
        return cache["pr_item_partner"][pr_item]

    fields = ["purchase_order_item", "sales_order", "sales_order_item", "material_request", "material_request_item"]
    if _has_field("Purchase Receipt Item", "sales_partner", cache["field_exists"]):
        fields = ["sales_partner"] + fields

    row = _db_get_value("Purchase Receipt Item", pr_item, fields, as_dict=True) or {}

    partner = _as_text(row.get("sales_partner"))
    if not partner:
        partner = (
            _get_po_item_partner(row.get("purchase_order_item"), cache)
            or _get_so_item_partner(row.get("sales_order_item"), cache)
            or _get_so_partner(row.get("sales_order"), cache)
            or _get_mr_item_partner(row.get("material_request_item"), cache)
            or _get_mr_partner(row.get("material_request"), cache)
        )

    cache["pr_item_partner"][pr_item] = partner
    return partner


def _new_cache() -> dict:
    return {
        "field_exists": {},
        "so_partner": {},
        "mr_partner": {},
        "so_item_partner": {},
        "mr_item_partner": {},
        "po_item_partner": {},
        "pr_item_partner": {},
    }


def sync_material_request_sales_partner(doc, method=None):
    if not getattr(doc, "items", None):
        return

    cache = _new_cache()
    row_partners: list[str] = []

    for row in doc.items:
        partner = (
            _get_so_item_partner(getattr(row, "sales_order_item", None), cache)
            or _get_so_partner(getattr(row, "sales_order", None), cache)
            or _as_text(getattr(row, "sales_partner", None))
            or _as_text(getattr(doc, "custom_sales_partner", None))
        )
        _set_row_sales_partner(row, partner)
        if partner:
            row_partners.append(partner)

    if hasattr(doc, "custom_sales_partner"):
        unique = list(dict.fromkeys(row_partners))
        if len(unique) == 1:
            doc.custom_sales_partner = unique[0]


def sync_purchase_order_sales_partner(doc, method=None):
    if not getattr(doc, "items", None):
        return

    cache = _new_cache()

    for row in doc.items:
        partner = (
            _get_so_item_partner(getattr(row, "sales_order_item", None), cache)
            or _get_so_partner(getattr(row, "sales_order", None), cache)
            or _get_mr_item_partner(getattr(row, "material_request_item", None), cache)
            or _get_mr_partner(getattr(row, "material_request", None), cache)
            or _as_text(getattr(row, "sales_partner", None))
        )
        _set_row_sales_partner(row, partner)


def sync_purchase_receipt_sales_partner(doc, method=None):
    if not getattr(doc, "items", None):
        return

    cache = _new_cache()

    for row in doc.items:
        partner = (
            _get_po_item_partner(getattr(row, "purchase_order_item", None), cache)
            or _get_so_item_partner(getattr(row, "sales_order_item", None), cache)
            or _get_so_partner(getattr(row, "sales_order", None), cache)
            or _get_mr_item_partner(getattr(row, "material_request_item", None), cache)
            or _get_mr_partner(getattr(row, "material_request", None), cache)
            or _as_text(getattr(row, "sales_partner", None))
        )
        _set_row_sales_partner(row, partner)


def sync_purchase_invoice_sales_partner(doc, method=None):
    if not getattr(doc, "items", None):
        return

    cache = _new_cache()

    for row in doc.items:
        partner = (
            _get_pr_item_partner(getattr(row, "pr_detail", None), cache)
            or _get_po_item_partner(getattr(row, "po_detail", None), cache)
            or _get_so_item_partner(getattr(row, "sales_order_item", None), cache)
            or _get_so_partner(getattr(row, "sales_order", None), cache)
            or _get_mr_item_partner(getattr(row, "material_request_item", None), cache)
            or _get_mr_partner(getattr(row, "material_request", None), cache)
            or _as_text(getattr(row, "sales_partner", None))
        )
        _set_row_sales_partner(row, partner)
