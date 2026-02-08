// Delivery Note: when created from Sales Order, append SO.custom_delivery_bom
//                as Delivery Note Items (item_code, item_name, description, qty)

function get_source_sales_order(frm) {
  const it = (frm.doc.items || [])[0];
  return it && (it.against_sales_order || it.sales_order) || null;
}

function append_dn_items_from_rows(frm, rows) {
  if (!Array.isArray(rows) || !rows.length) return;
  // default warehouse from existing DN items (if any)
  const default_wh = (frm.doc.items || []).find(x => x.warehouse)?.warehouse || null;
  const existing = new Set(
    (frm.doc.items || [])
      .filter(x => x.item_code)
      .map(x => `${x.item_code}||${x.qty || 0}||${(x.description || "").trim()}`)
  );

  rows.forEach(r => {
    const key = `${r.item}||${r.qty || 0}||${(r.description || "").trim()}`;
    if (existing.has(key)) return;
    const d = frm.add_child("items");
    d.item_code   = r.item;
    d.item_name   = r.item_name || r.item;
    d.description = r.description || "";
    d.qty         = r.qty || 0;
    if ("uom" in d) d.uom = r.uom || d.uom;
    if ("conversion_factor" in d) d.conversion_factor = r.conversion_factor || 1;
    if (default_wh) d.warehouse = default_wh;
  });
  frm.refresh_field("items");
}

frappe.ui.form.on("Delivery Note", {
  onload_post_render(frm) {
    const so = get_source_sales_order(frm);
    if (!so || frm._delivery_bom_appended) return;
    frm._delivery_bom_appended = true;
    frappe.call({
      method: "it.api.get_delivery_bom_from_sales_order",
      args: { sales_order_name: so },
      freeze: false
    }).then(r => append_dn_items_from_rows(frm, r && r.message));
  }
});
