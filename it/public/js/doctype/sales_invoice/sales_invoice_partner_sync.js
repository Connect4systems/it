function sync_sales_partner_to_items(frm) {
  const partner = frm.doc.sales_partner || null;
  const rows = frm.doc.items || [];
  let changed = false;

  rows.forEach((row) => {
    if (row.sales_partner !== partner) {
      row.sales_partner = partner;
      changed = true;
    }
  });

  if (changed) {
    frm.refresh_field("items");
  }
}

frappe.ui.form.on("Sales Invoice", {
  onload_post_render(frm) {
    sync_sales_partner_to_items(frm);
  },

  sales_partner(frm) {
    sync_sales_partner_to_items(frm);
  },

  items_add(frm) {
    sync_sales_partner_to_items(frm);
  },

  validate(frm) {
    sync_sales_partner_to_items(frm);
  }
});
