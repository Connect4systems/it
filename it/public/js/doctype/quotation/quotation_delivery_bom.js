function fill_qtn_delivery_bom(frm, rows) {
  if (!Array.isArray(rows) || !rows.length) return;
  frm.doc.custom_delivery_bom = [];
  rows.forEach(r => {
    const d = frm.add_child("custom_delivery_bom");
    d.item        = r.item;
    d.item_name   = r.item_name || r.item;
    d.description = r.description || "";
    d.qty         = r.qty || 0;
    // pass through parent tracking if the field exists on your child doctype
    if ("custom_parent_product" in d) d.custom_parent_product = r.custom_parent_product || null;
  });
  frm.refresh_field("custom_delivery_bom");
}

frappe.ui.form.on("Quotation", {
  onload_post_render(frm) {
    if (!frm.doc.opportunity) return;
    const empty = !(frm.doc.custom_delivery_bom || []).length;
    if (empty && !frm._delivery_bom_filled) {
      frm._delivery_bom_filled = true;
      frappe.call({
        method: "it.api.get_delivery_bom_from_opportunity_bundle",
        args: { opportunity_name: frm.doc.opportunity },
        freeze: false
      }).then(r => fill_qtn_delivery_bom(frm, r && r.message));
    }
  }
});
