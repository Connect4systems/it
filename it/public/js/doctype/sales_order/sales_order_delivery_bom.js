// Sales Order: when created from Quotation, copy Quotation.custom_delivery_bom
//              into Sales Order.custom_delivery_bom

function fill_so_delivery_bom(frm, rows) {
  if (!Array.isArray(rows) || !rows.length) return;
  frm.doc.custom_delivery_bom = [];
  rows.forEach(r => {
    const d = frm.add_child("custom_delivery_bom");
    d.item        = r.item;
    d.item_name   = r.item_name || r.item;
    d.description = r.description || "";
    d.qty         = r.qty || 0;
  });
  frm.refresh_field("custom_delivery_bom");
}

function get_source_quotation(frm) {
  // try read from first item mapping
  const it = (frm.doc.items || [])[0];
  return it && (it.prevdoc_docname || it.quotation) || null;
}

frappe.ui.form.on("Sales Order", {
  onload_post_render(frm) {
    const qtn = get_source_quotation(frm);
    if (!qtn) return;
    const empty = !(frm.doc.custom_delivery_bom || []).length;
    if (empty && !frm._delivery_bom_filled) {
      frm._delivery_bom_filled = true;
      frappe.call({
        method: "it.api.get_delivery_bom_from_quotation",
        args: { quotation_name: qtn },
        freeze: false
      }).then(r => fill_so_delivery_bom(frm, r && r.message));
    }
  }
});
