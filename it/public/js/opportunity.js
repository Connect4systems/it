// Fieldnames (edit if yours differ)
var PARENT_BUNDLE_TABLE = "custom_product_bundle";
var CHILD_DOCTYPE       = "Product Bundle Item";
var PRODUCT_FIELD       = "custom_product";          // link to parent item
var CHILD_TOTAL_FIELD   = "custom_total_cost";       // TOTAL = custom_cost * qty
var PARENT_COST_TOTAL   = "custom_purchase_rate";    // parent field to set

function toFlt(v){ var n=parseFloat(v); return isNaN(n)?0:n; }

frappe.ui.form.on("Opportunity", {
  refresh(frm){
    // Filter bundles by the parent row's item_code (Product Bundle.new_item_code == item_code)
    frm.set_query("custom_product_bundle","items",function(doc, cdt, cdn){
      var r = locals[cdt][cdn] || {};
      if (!r.item_code) return {filters:{name:["=","___none___"]}};
      return {filters:{new_item_code:r.item_code}};
    });
  },
  onload_post_render(frm){ rollup(frm); },
  validate(frm){ rollup(frm); }
});

frappe.ui.form.on("Opportunity Item", {
  // When user picks a bundle, build child rows with PER-ONE qty
  custom_product_bundle(frm, cdt, cdn){
    var row = locals[cdt][cdn] || {};
    if (!row.item_code || !row.custom_product_bundle) return;
    var parent_item = row.item_code;

    frappe.db.get_doc("Product Bundle", row.custom_product_bundle).then(function(pb){
      // remove existing rows for this parent product
      var keep = [];
      (frm.doc[PARENT_BUNDLE_TABLE] || []).forEach(function(ch){
        if (ch[PRODUCT_FIELD] !== parent_item) keep.push(ch);
      });
      frm.doc[PARENT_BUNDLE_TABLE] = keep;

      // add components (per-one qty)
      (pb.items || []).forEach(function(c){
        if (!c.item_code) return;
        var raw = toFlt(c.qty) || 0; if (!raw) return;

        var ch = frm.add_child(PARENT_BUNDLE_TABLE);
        frappe.model.set_value(ch.doctype, ch.name, "item_code", c.item_code);
        frappe.model.set_value(ch.doctype, ch.name, "description", c.description || "");
        frappe.model.set_value(ch.doctype, ch.name, "uom", c.uom || "");
        frappe.model.set_value(ch.doctype, ch.name, "qty", raw);           // PER-ONE
        frappe.model.set_value(ch.doctype, ch.name, PRODUCT_FIELD, parent_item);

        // seed custom_cost from Item rates if blank
        frappe.db.get_value("Item", c.item_code, ["last_purchase_rate","valuation_rate"]).then(function(r){
          var unit = toFlt(r && r.message ? r.message.last_purchase_rate : 0) ||
                     toFlt(r && r.message ? r.message.valuation_rate : 0);
          if (!toFlt(ch["custom_cost"])) {
            frappe.model.set_value(ch.doctype, ch.name, "custom_cost", unit);
          }
          normalize_child(ch.doctype, ch.name);
          rollup(frm);
        });
      });

      frm.refresh_field(PARENT_BUNDLE_TABLE);
      rollup(frm);
    });
  },

  // Changing qty/rate on parent doesn't affect child per-one rows; just recompute margin
  qty(frm){ rollup(frm); },
  rate(frm){ rollup(frm); }
});

// Child row live math
frappe.ui.form.on(CHILD_DOCTYPE, {
  qty(frm, cdt, cdn){ normalize_child(cdt, cdn); frm.refresh_field(PARENT_BUNDLE_TABLE); rollup(frm); },
  custom_cost(frm, cdt, cdn){ normalize_child(cdt, cdn); frm.refresh_field(PARENT_BUNDLE_TABLE); rollup(frm); }
});

function normalize_child(cdt, cdn){
  var ch   = locals[cdt][cdn] || {};
  var qty  = toFlt(ch.qty) || 0;
  var unit = toFlt(ch["custom_cost"] || 0);
  frappe.model.set_value(cdt, cdn, CHILD_TOTAL_FIELD, qty * unit);   // TOTAL = custom_cost * qty
}

function rollup(frm){
  // Σ(child TOTAL) per parent item
  var perParent = {}, hasRows = {};
  (frm.doc[PARENT_BUNDLE_TABLE] || []).forEach(function(ch){
    var p = ch[PRODUCT_FIELD]; if (!p) return;
    hasRows[p] = true;
    perParent[p] = (perParent[p] || 0) + toFlt(ch[CHILD_TOTAL_FIELD] || 0);
  });

  // Write parent totals & margins for ALL items
  (frm.doc.items || []).forEach(function(r){
    if (!r.item_code) return;
    var rate = toFlt(r.rate) || 0;
    var cpr  = hasRows[r.item_code] ? (perParent[r.item_code] || 0) : toFlt(r[PARENT_COST_TOTAL] || 0);

    if (PARENT_COST_TOTAL in r) r[PARENT_COST_TOTAL] = cpr;
    if ("custom_margin" in r)   r.custom_margin = rate ? ((rate - cpr) / rate) * 100 : 0;
  });

  frm.refresh_field("items");
}
