from frappe.utils import flt

from erpnext.accounts.doctype.sales_invoice.sales_invoice import (
    SalesInvoice as ERPNextSalesInvoice,
)
from erpnext.accounts.general_ledger import (
    get_round_off_account_and_cost_center,
)


class CustomSalesInvoice(ERPNextSalesInvoice):
    def get_gl_entries(self, warehouse_account=None):
        gl_entries = super().get_gl_entries(warehouse_account)

        company_precision = self.precision("base_grand_total")
        company_smallest_unit = 1 / (10**company_precision)

        total_debit = 0
        total_credit = 0

        for entry in gl_entries:
            total_debit += flt(entry.get("debit"))
            total_credit += flt(entry.get("credit"))

        imbalance = flt(
            total_debit - total_credit,
            company_precision,
        )

        if not imbalance:
            return gl_entries

        transaction_difference = 0

        if self.taxes:
            final_tax_total = flt(self.taxes[-1].total)

            transaction_difference = flt(
                flt(self.grand_total) - final_tax_total,
                self.precision("grand_total"),
            )

        converted_transaction_difference = abs(
            flt(
                transaction_difference * flt(self.conversion_rate),
                company_precision,
            )
        )

        net_total_precision_loss = abs(
            flt(
                flt(self.get("base_net_total"))
                - flt(
                    flt(self.get("net_total")) * flt(self.conversion_rate),
                    self.precision("net_total"),
                ),
                company_precision,
            )
        )

        conversion_precision_loss = abs(
            flt(
                flt(self.get("base_grand_total"))
                - (
                    flt(self.get("grand_total"))
                    * flt(self.conversion_rate)
                ),
                company_precision,
            )
        )

        allowed_difference = flt(
            converted_transaction_difference
            + net_total_precision_loss
            + conversion_precision_loss
            + company_smallest_unit,
            company_precision,
        )

        # Do not use this correction for material accounting differences.
        if not transaction_difference or abs(imbalance) > allowed_difference:
            return gl_entries

        (
            round_off_account,
            round_off_cost_center,
            _round_off_for_opening,
        ) = get_round_off_account_and_cost_center(
            self.company,
            self.doctype,
            self.name,
            self.use_company_roundoff_cost_center,
        )

        existing_precision_entry = None

        for entry in gl_entries:
            if (
                entry.get("account") == round_off_account
                and entry.get("remarks")
                == "Net total calculation precision loss"
            ):
                existing_precision_entry = entry
                break

        if existing_precision_entry:
            current_net_amount = flt(
                flt(existing_precision_entry.get("debit"))
                - flt(existing_precision_entry.get("credit")),
                company_precision,
            )

            corrected_net_amount = flt(
                current_net_amount - imbalance,
                company_precision,
            )

            existing_precision_entry["debit"] = (
                corrected_net_amount
                if corrected_net_amount > 0
                else 0
            )
            existing_precision_entry["credit"] = (
                abs(corrected_net_amount)
                if corrected_net_amount < 0
                else 0
            )

            existing_precision_entry["debit_in_account_currency"] = (
                existing_precision_entry["debit"]
            )
            existing_precision_entry["credit_in_account_currency"] = (
                existing_precision_entry["credit"]
            )
            existing_precision_entry["remarks"] = (
                "Grand total currency precision adjustment"
            )

            self.set_transaction_currency_and_rate_in_gl_map(
                [existing_precision_entry]
            )

            return gl_entries

        adjustment = self.get_gl_dict(
            {
                "account": round_off_account,
                "against": self.customer,
                "debit": abs(imbalance) if imbalance < 0 else 0,
                "credit": imbalance if imbalance > 0 else 0,
                "cost_center": (
                    round_off_cost_center
                    if self.use_company_roundoff_cost_center
                    else self.cost_center or round_off_cost_center
                ),
                "remarks": "Grand total currency precision adjustment",
            }
        )

        gl_entries.append(adjustment)
        self.set_transaction_currency_and_rate_in_gl_map([adjustment])

        return gl_entries
