from __future__ import annotations
import frappe
from frappe.utils import flt

# ---- fieldnames (edit if different) ----
PARENT_BUNDLE_TABLE = "custom_product_bundle"      # child table field on Opportunity
CHILD_DOCTYPE       = "Product Bundle Item"        # child doctype name
PRODUCT_FIELD       = "custom_product"             # child column linking to parent item (Link -> Item)
CHILD_TOTAL_FIELD   = "custom_total_cost"          # TOTAL = custom_cost * qty
PARENT_COST_TOTAL   = "custom_purchase_rate"       # parent field to write (Σ child totals)
MARGIN_FIELD        = "custom_margin"
# ----------------------------------------

def _has(meta, fieldname: str) -> bool:
    return bool(meta.get_field(fieldname))

def on_validate(doc, method=None):
    """
    - Build Product Bundle rows (once) using per-one quantities.
    - Compute each child custom_total_cost = custom_cost * qty.
    - Roll up Σ(child totals) into Opportunity Item.custom_purchase_rate.
    - Compute custom_margin = ((rate - custom_purchase_rate) / rate) * 100 for EVERY row.
    """
    if not hasattr(doc, PARENT_BUNDLE_TABLE):
        return

    opp_meta       = frappe.get_meta("Opportunity")
    item_meta      = frappe.get_meta("Opportunity Item")
    child_meta     = frappe.get_meta(CHILD_DOCTYPE)

    # If the critical child link field is missing, we can't relate rows -> parent item.
    if not _has(child_meta, PRODUCT_FIELD):
        # Nothing to do; keep silent so saving still works.
        return

    # Map: parent item code -> selected bundle (to (re)build child rows)
    bundle_of: dict[str, str] = {}
    for it in (doc.items or []):
        code = getattr(it, "item_code", None)
        bun  = getattr(it, "custom_product_bundle", None) if _has(item_meta, "custom_product_bundle") else None
        if code and bun:
            bundle_of[code] = bun

    # Existing child rows grouped by parent product
    rows_by_product: dict[str, list] = {}
    for ch in (getattr(doc, PARENT_BUNDLE_TABLE) or []):
        p = getattr(ch, PRODUCT_FIELD, None)
        if p:
            rows_by_product.setdefault(p, []).append(ch)

    # (Re)build children only if missing or bundle components changed
    for parent_item, bundle_name in bundle_of.items():
        existing = rows_by_product.get(parent_item, []) or []
        need_rebuild = False
        compset = set()
        try:
            pb = frappe.get_doc("Product Bundle", bundle_name)
            compset = {i.item_code for i in (pb.items or []) if i.item_code}
        except Exception:
            pb = None

        existset = {getattr(r, "item_code", None) for r in existing if getattr(r, "item_code", None)}
        if not existing or (pb and compset and compset != existset):
            need_rebuild = True

        if need_rebuild and pb:
            # remove any previous rows for this parent product
            keep = []
            for ch in (getattr(doc, PARENT_BUNDLE_TABLE) or []):
                if getattr(ch, PRODUCT_FIELD, None) != parent_item:
                    keep.append(ch)
            setattr(doc, PARENT_BUNDLE_TABLE, keep)

            # add components with PER-ONE quantities only
            for comp in (pb.items or []):
                if not comp.item_code:
                    continue
                raw = flt(getattr(comp, "qty", 0))
                if raw <= 0:
                    continue
                ch = doc.append(PARENT_BUNDLE_TABLE, {})
                ch.item_code   = comp.item_code
                ch.description = getattr(comp, "description", "") or ""
                ch.uom         = getattr(comp, "uom", "") or ""
                ch.qty         = raw                      # per-one qty (not scaled by parent qty)
                setattr(ch, PRODUCT_FIELD, parent_item)

            rows_by_product[parent_item] = [r for r in getattr(doc, PARENT_BUNDLE_TABLE)
                                            if getattr(r, PRODUCT_FIELD, None) == parent_item]

    # ---- child totals & rollup ----
    per_parent_sum: dict[str, float] = {}

    for ch in (getattr(doc, PARENT_BUNDLE_TABLE) or []):
        parent_item = getattr(ch, PRODUCT_FIELD, None)
        if not parent_item:
            continue

        qty  = flt(getattr(ch, "qty", 0))                 # per-one qty
        unit = flt(getattr(ch, "custom_cost", 0))         # unit cost (user editable)
        line_total = qty * unit

        if _has(child_meta, CHILD_TOTAL_FIELD):
            setattr(ch, CHILD_TOTAL_FIELD, line_total)

        per_parent_sum[parent_item] = per_parent_sum.get(parent_item, 0.0) + line_total

    # Write parent totals & margins for ALL rows
    for it in (doc.items or []):
        item = getattr(it, "item_code", None)
        rate = flt(getattr(it, "rate", 0))
        if not item:
            continue

        # bundled: overwrite; single item: keep user's value (if field exists)
        if item in per_parent_sum:
            cpr = per_parent_sum[item]                      # Σ(child totals), independent of parent qty
            if _has(item_meta, PARENT_COST_TOTAL):
                setattr(it, PARENT_COST_TOTAL, cpr)
        else:
            cpr = flt(getattr(it, PARENT_COST_TOTAL, 0)) if _has(item_meta, PARENT_COST_TOTAL) else 0

        if _has(item_meta, MARGIN_FIELD):
            setattr(it, MARGIN_FIELD, ((rate - cpr) / rate * 100.0) if rate else 0.0)
