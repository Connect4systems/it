// Minimal helper
function flt(v){ v = parseFloat(v); return isNaN(v) ? 0 : v; }

frappe.ui.form.on("Opportunity", {
  // last client step before the framework runs its missing-fields check
  before_save(frm){
    (frm.doc.items || []).forEach(function(row){
      // normalize to numbers, never null/undefined
      if (row.rate == null) row.rate = 0;
      if (row.base_rate == null) row.base_rate = 0;

      // qty too
      var qty = flt(row.qty);
      var rate = flt(row.rate);
      var base_rate = flt(row.base_rate);

      // compute amounts so mandatory base fields are never empty
      row.amount = qty * rate;
      row.base_amount = qty * base_rate;
    });

    frm.refresh_field("items");
  },

  // optional: keep the grid from creating nulls in the first place
  items_add(frm, cdt, cdn){
    frappe.model.set_value(cdt, cdn, "rate", 0);
    frappe.model.set_value(cdt, cdn, "base_rate", 0);
    frappe.model.set_value(cdt, cdn, "amount", 0);
    frappe.model.set_value(cdt, cdn, "base_amount", 0);
  }
});
