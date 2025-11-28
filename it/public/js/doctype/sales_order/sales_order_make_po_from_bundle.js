// Sales Order → Create → Purchase Order (Bundle Items)
// Builds a PO from Sales Order.custom_delivery_bom ONLY (excludes parent product lines)

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
