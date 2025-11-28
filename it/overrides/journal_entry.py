# /apps/it/it/overrides/journal_entry.py

import frappe
from erpnext.accounts.utils import get_outstanding_invoices as original_get_outstanding_invoices

@frappe.whitelist()
def get_all_sales_invoices(doctype, party_type, party, account=None, condition=None):
    if doctype != "Sales Invoice":
        return original_get_outstanding_invoices(doctype, party_type, party, account, condition)

    return frappe.db.get_all("Sales Invoice",
        filters={
            "docstatus": 1,
            "customer": party
        },
        fields=["name", "posting_date", "grand_total", "outstanding_amount"],
        order_by="posting_date desc"
    )
