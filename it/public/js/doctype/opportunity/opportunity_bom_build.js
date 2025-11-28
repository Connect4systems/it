// IT • Opportunity — Bundle-driven cost + price-edit + cost-based margin
// + Filter Product Bundle to only 'main' products
// + Allow manual cost edit on non-main rows (custom_purchase_rate)

(function () {
  // ---- constants for your schema ----
  const BUNDLE_TABLE = "custom_product_bundle";
  const B_PARENT     = "custom_product";
  const B_QTY        = "qty";
  const B_COST       = "custom_cost";
  const B_TOTAL      = "custom_total_cost";

  const I_CODE       = "item_code";
  const I_QTY        = "qty";
  const I_RATE       = "rate";
  const I_COST_PER   = "custom_purchase_rate";
  const I_TOTAL_COST = "custom_total_cost";
  const I_MARGIN_PCT = "custom_margin";
  const I_MAIN       = "custom_main"; // checkbox on Opportunity Item

  const H_OVERHEAD   = "custom_overhead";
  const H_TOTAL_COST = "custom_total_cost";
  const H_TOTAL_SELL = "total";
  const H_PROFIT     = "custom_total_profit";
  const H_MARGIN_PCT = "custom_profit_margin"; // (total - total_cost) / total_cost * 100

  const flt  = (v) => (isNaN(parseFloat(v)) ? 0 : parseFloat(v));
  const s    = (v) => (v || "").trim();
  const cint = (v) => (isNaN(parseInt(v)) ? 0 : parseInt(v));

  // ---- helper: show only checked main items in bundle product picker ----
  function get_main_item_codes(frm) {
    return (frm.doc.items || [])
      .filter((it) => !!it[I_MAIN])
      .map((it) => s(it[I_CODE]))
      .filter(Boolean);
  }

  function set_bundle_product_query(frm) {
    const allowed = get_main_item_codes(frm);
    frm.set_query(B_PARENT, BUNDLE_TABLE, () => {
      return {
        filters: allowed.length
          ? { name: ["in", allowed] }
          : { name: ["in", ["__none__"]] }, // empty when nothing is checked
      };
    });
  }

  // allow saving with 0 prices
  function relax_selling_mandatory(frm) {
    const grid = frm.get_field("items")?.grid;
    if (!grid) return;
    ["rate", "amount", "base_rate", "base_amount"].forEach((f) => {
      try { grid.toggle_reqd(f, false); } catch (e) {}
      const gf = grid.grid_fields_dict?.[f];
      if (gf?.df) { gf.df.reqd = 0; gf.refresh(); }
    });
  }

  // === Per-row editability for custom_purchase_rate ===
  function toggle_cost_edit(frm, row) {
    const editable = !cint(row[I_MAIN] || 0);
    frm.fields_dict["items"]?.grid?.toggle_enable(I_COST_PER, editable, row.name);
  }

  function apply_editability_for_all(frm) {
    (frm.doc.items || []).forEach((r) => toggle_cost_edit(frm, r));
    frm.refresh_field("items");
  }

  // --- bundle row math ---
  function recompute_bundle_row_total(r) {
    r[B_TOTAL] = flt(r[B_QTY]) * flt(r[B_COST]);
  }
  function recompute_all_bundle_totals(frm) {
    (frm.doc[BUNDLE_TABLE] || []).forEach(recompute_bundle_row_total);
    frm.refresh_field(BUNDLE_TABLE);
  }

  // --- build per-parent (per ONE unit) cost map from bundle ---
  function map_cost_per_parent(frm) {
    const perParent = {};
    (frm.doc[BUNDLE_TABLE] || []).forEach((r) => {
      const parent = s(r[B_PARENT]);
      if (!parent) return;
      perParent[parent] = (perParent[parent] || 0) + flt(r[B_QTY]) * flt(r[B_COST]);
    });
    return perParent;
  }

  // --- push costs: ONLY overwrite for MAIN rows; non-main keeps manual cost ---
  function push_costs_from_bundle(frm) {
    const perParent = map_cost_per_parent(frm);

    (frm.doc.items || []).forEach((it) => {
      const parentCode   = s(it[I_CODE]);
      const bundlePerOne = perParent[parentCode] || 0;

      let perUnitCost;
      if (cint(it[I_MAIN])) {
        // Main product -> always from bundle
        perUnitCost = bundlePerOne;
        it[I_COST_PER] = perUnitCost;
      } else {
        // Non-main -> keep user-entered cost
        perUnitCost = flt(it[I_COST_PER]);
      }

      it[I_TOTAL_COST] = flt(it[I_QTY]) * perUnitCost;
      toggle_cost_edit(frm, it); // keep UI state in sync
    });

    frm.refresh_field("items");
  }

  // --- line margin (based on selling rate vs cost) ---
  function recompute_item_margins(frm) {
    (frm.doc.items || []).forEach((it) => {
      const rate = flt(it[I_RATE]);
      const cost = flt(it[I_COST_PER]);
      it[I_MARGIN_PCT] = rate > 0 ? ((rate - cost) / rate) * 100.0 : 0;
    });
    frm.refresh_field("items");
  }

  // --- header totals & cost-based margin ---
  function recompute_header_totals(frm) {
    let total_cost = 0;
    (frm.doc.items || []).forEach((it) => total_cost += flt(it[I_TOTAL_COST]));
    total_cost += flt(frm.doc[H_OVERHEAD]);
    frm.set_value(H_TOTAL_COST, total_cost);

    const total_sell = flt(frm.doc[H_TOTAL_SELL]);
    const profit = total_sell - total_cost;
    frm.set_value(H_PROFIT, profit);

    // (total - total_cost) / total_cost * 100
    const cost_pct = total_cost > 0 ? (profit / total_cost) * 100.0 : 0;
    frm.set_value(H_MARGIN_PCT, cost_pct);
  }

  function bind_bundle_grid(frm) {
    const g = frm.get_field(BUNDLE_TABLE)?.grid;
    if (!g || g._it_bound) return;
    g._it_bound = true;

    ["change", "row-add", "row-remove"].forEach((ev) => {
      g.on(ev, () => {
        recompute_all_bundle_totals(frm);
        push_costs_from_bundle(frm);
        recompute_item_margins(frm);
        recompute_header_totals(frm);
      });
    });
  }

  // ---- doctype wiring ----
  frappe.ui.form.on("Opportunity", {
    setup(frm) {
      relax_selling_mandatory(frm);
      set_bundle_product_query(frm);
    },
    onload_post_render(frm) {
      relax_selling_mandatory(frm);
      set_bundle_product_query(frm);
    },
    refresh(frm) {
      relax_selling_mandatory(frm);
      set_bundle_product_query(frm);
      bind_bundle_grid(frm);

      // full recompute on load
      recompute_all_bundle_totals(frm);
      push_costs_from_bundle(frm);
      recompute_item_margins(frm);
      recompute_header_totals(frm);

      // ensure editable states are correct
      apply_editability_for_all(frm);
    },
    [H_OVERHEAD](frm) {
      recompute_header_totals(frm);
    },
    before_save(frm) {
      // keep everything consistent
      recompute_all_bundle_totals(frm);
      push_costs_from_bundle(frm);
      recompute_item_margins(frm);
      recompute_header_totals(frm);
    },
  });

  // Items table reactions
  frappe.ui.form.on("Opportunity Item", {
    // user toggles main -> update query + editability + costs
    [I_MAIN](frm, cdt, cdn) {
      const row = locals[cdt][cdn];
      toggle_cost_edit(frm, row);
      set_bundle_product_query(frm);
      push_costs_from_bundle(frm);
      recompute_item_margins(frm);
      recompute_header_totals(frm);
    },

    // qty/rate changes always reflect totals
    [I_QTY](frm) {
      push_costs_from_bundle(frm);
      recompute_item_margins(frm);
      recompute_header_totals(frm);
    },
    [I_RATE](frm) {
      recompute_item_margins(frm);
      recompute_header_totals(frm);
    },

    // manual edit of non-main cost should recalc line & header
    [I_COST_PER](frm, cdt, cdn) {
      const row = locals[cdt][cdn];
      // if user edits a MAIN row by any chance, UI will be locked; but still guard:
      if (!cint(row[I_MAIN])) {
        row[I_TOTAL_COST] = flt(row[I_QTY]) * flt(row[I_COST_PER]);
        frm.refresh_field("items");
        recompute_item_margins(frm);
        recompute_header_totals(frm);
      }
    },

    items_add(frm, cdt, cdn) {
      const row = locals[cdt][cdn];
      // enforce editability immediately for new row
      toggle_cost_edit(frm, row);
    },
  });

  // Bundle child quick hooks (if child doctype name is "Product Bundle Item")
  frappe.ui.form.on("Product Bundle Item", {
    [B_QTY](frm, cdt, cdn)  {
      const r = locals[cdt][cdn];
      recompute_bundle_row_total(r);
      frm.refresh_field(BUNDLE_TABLE);
      push_costs_from_bundle(frm);
      recompute_item_margins(frm);
      recompute_header_totals(frm);
    },
    [B_COST](frm, cdt, cdn) {
      const r = locals[cdt][cdn];
      recompute_bundle_row_total(r);
      frm.refresh_field(BUNDLE_TABLE);
      push_costs_from_bundle(frm);
      recompute_item_margins(frm);
      recompute_header_totals(frm);
    },
    [B_PARENT](frm) {
      push_costs_from_bundle(frm);
      recompute_item_margins(frm);
      recompute_header_totals(frm);
    },
  });
})();
