// Sales Order bundle helpers (Delivery BOM carry + PO create button)
// Includes logic from sales_order_delivery_bom.js and sales_order_make_po_from_bundle.js

// --- carry Quotation.custom_delivery_bom -> Sales Order.custom_delivery_bom ---
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

// --- custom Create → Purchase Order (Bundle Items) ---
frappe.ui.form.on("Sales Order", {
  refresh(frm) {
    if (frm.is_new()) return;
    // show under the Create menu
    frm.add_custom_button(__("Purchase Order (Bundle Items)"), () => {
      frappe.call({
        method: "it.api.make_purchase_order_from_so_bundle",
        args: { sales_order: frm.doc.name },
        freeze: true
      }).then(r => {
        if (!r || !r.message) return;
        // open returned unsaved PO
        frappe.model.with_doctype("Purchase Order", () => {
          const docs = frappe.model.sync(r.message);
          const po = docs && docs[0];
          if (po) frappe.set_route("Form", po.doctype, po.name);
        });
      });
    }, __("Create"));
  }
});
