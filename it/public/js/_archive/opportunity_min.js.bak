function flt(v){ v=parseFloat(v); return isNaN(v)?0:v; }

frappe.ui.form.on("Opportunity Item", {
  item_code(frm, cdt, cdn){ ensure_amount(cdt, cdn); },
  qty(frm, cdt, cdn){ ensure_amount(cdt, cdn); },
  rate(frm, cdt, cdn){ ensure_amount(cdt, cdn); }
});

function ensure_amount(cdt, cdn){
  const r = locals[cdt][cdn] || {};
  if (r.rate == null) frappe.model.set_value(cdt, cdn, "rate", 0);
  if (r.base_rate == null) frappe.model.set_value(cdt, cdn, "base_rate", flt(r.rate));
  const qty = flt(r.qty), rate = flt(r.rate);
  frappe.model.set_value(cdt, cdn, "amount", qty * rate);
  frappe.model.set_value(cdt, cdn, "base_amount", qty * flt(r.base_rate));
}
