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
function build_po_selection_dialog(frm) {
  const soItems = (frm.doc.items || []).map(it => ({
    key: `SO-${it.name}`,
    source: "SO",
    item_code: it.item_code,
    item_name: it.item_name,
    description: it.description || "",
    qty: it.qty || 0,
    uom: it.uom,
    conversion_factor: it.conversion_factor || 1,
    sales_order_item: it.name
  }));

  const bomItems = (frm.doc.custom_delivery_bom || []).map((r, idx) => ({
    key: `BOM-${idx}`,
    source: "BOM",
    item_code: r.item,
    item_name: r.item_name || r.item,
    description: r.description || "",
    qty: r.qty || 0,
    uom: r.uom,
    conversion_factor: 1,
    sales_order_item: null
  }));

  const rows = [...soItems, ...bomItems].filter(r => r.item_code);

  const d = new frappe.ui.Dialog({
    title: __("Select Items (SO + Delivery BOM)"),
    fields: [
      { fieldtype: "Check", fieldname: "select_all", label: __("Select All"), default: 1 },
      { fieldtype: "HTML", fieldname: "items_html" }
    ],
    primary_action_label: __("Create Purchase Order"),
    primary_action() {
      const selected = [];
      d.$wrapper.find(".it-po-row").each(function () {
        const $row = $(this);
        const checked = $row.find(".it-po-check").is(":checked");
        if (!checked) return;
        const qty = parseFloat($row.find(".it-po-qty").val()) || 0;
        if (qty <= 0) return;
        const data = $row.data("item");
        selected.push({
          source: data.source,
          item_code: data.item_code,
          item_name: data.item_name,
          description: data.description,
          qty: qty,
          uom: data.uom,
          conversion_factor: data.conversion_factor,
          sales_order_item: data.sales_order_item
        });
      });

      if (!selected.length) {
        frappe.msgprint(__("Please select at least one item."));
        return;
      }

      frappe.call({
        method: "it.api.make_purchase_order_from_so_selection",
        args: { sales_order: frm.doc.name, selections: selected },
        freeze: true
      }).then(r => {
        if (!r || !r.message) return;
        frappe.model.with_doctype("Purchase Order", () => {
          const docs = frappe.model.sync(r.message);
          const po = docs && docs[0];
          if (po) frappe.set_route("Form", po.doctype, po.name);
        });
      });

      d.hide();
    }
  });

  const html = [
    '<div class="it-po-table">',
    '<table class="table table-bordered">',
    '<thead><tr>',
    '<th style="width:40px"></th>',
    `<th>${__("Source")}</th>`,
    `<th>${__("Item")}</th>`,
    `<th>${__("Item Name")}</th>`,
    `<th>${__("Qty")}</th>`,
    '</tr></thead><tbody>',
    rows.map(r => {
      return `
        <tr class="it-po-row" data-item='${JSON.stringify(r).replace(/'/g, "&#39;")}'>
          <td><input type="checkbox" class="it-po-check" checked></td>
          <td>${frappe.utils.escape_html(r.source)}</td>
          <td>${frappe.utils.escape_html(r.item_code)}</td>
          <td>${frappe.utils.escape_html(r.item_name || "")}</td>
          <td><input type="number" class="form-control it-po-qty" value="${r.qty}" min="0" step="0.01"></td>
        </tr>`;
    }).join(""),
    '</tbody></table></div>'
  ].join("");

  d.fields_dict.items_html.$wrapper.html(html);

  d.fields_dict.select_all.$input.on("change", () => {
    const checked = d.get_value("select_all");
    d.$wrapper.find(".it-po-check").prop("checked", checked);
  });

  d.show();
}

frappe.ui.form.on("Sales Order", {
  refresh(frm) {
    if (frm.is_new()) return;
    frm.add_custom_button(__("Purchase Order (SO + Delivery BOM)"), () => {
      build_po_selection_dialog(frm);
    }, __("Create"));
  }
});
