"""Invoice due-date forecast calculations, independent of the Frappe runtime."""

from datetime import date
from decimal import Decimal


def as_date(value):
	if isinstance(value, date):
		return date(value.year, value.month, value.day)
	return date.fromisoformat(str(value)[:10])


def get_months(from_date, to_date):
	start, end = as_date(from_date), as_date(to_date)
	if start > end:
		raise ValueError("From Date must not be after To Date")
	count = (end.year - start.year) * 12 + end.month - start.month + 1
	if count > 120:
		raise ValueError("Select a date range of at most 120 months")
	months = []
	for offset in range(count):
		year, month = divmod(start.year * 12 + start.month - 1 + offset, 12)
		months.append(date(year, month + 1, 1))
	return months


def month_key(value):
	return as_date(value).strftime("month_%Y_%m")


def get_bucket(due_date, from_date, to_date, include_earlier):
	if not due_date:
		return "undated"
	due_date = as_date(due_date)
	if due_date < as_date(from_date):
		return "earlier" if include_earlier else None
	if due_date > as_date(to_date):
		return None
	return month_key(due_date)


def company_amount(invoice, company_currency):
	# ERPNext stores outstanding_amount in the receivable/payable account currency.
	amount = Decimal(str(invoice["outstanding_amount"] or 0))
	account_currency = invoice.get("party_account_currency")
	if account_currency == company_currency:
		return amount
	if account_currency != invoice.get("currency"):
		raise ValueError(f"Cannot convert account currency for invoice {invoice['name']}")
	rate = Decimal(str(invoice.get("conversion_rate") or 0))
	if rate <= 0:
		raise ValueError(f"Missing positive conversion rate for invoice {invoice['name']}")
	return amount * rate


def build_rows(invoices, from_date, to_date, currency, include_earlier=True, show_details=True):
	months = get_months(from_date, to_date)
	keys = (["earlier"] if include_earlier else []) + [month_key(month) for month in months] + ["undated"]
	groups = []
	for doctype, label, sign in (
		("Sales Invoice", "Customer Receipts / Refunds", 1),
		("Purchase Invoice", "Supplier Payments / Refunds", -1),
	):
		parent = {
			"row_id": doctype,
			"flow": label,
			"indent": 0,
			"currency": currency,
		}
		totals = dict.fromkeys(keys, Decimal(0))
		children = []
		for invoice in invoices:
			if invoice["invoice_type"] != doctype:
				continue
			bucket = get_bucket(invoice.get("due_date"), from_date, to_date, include_earlier)
			if bucket is None:
				continue
			amount = company_amount(invoice, currency) * sign
			if not amount:
				continue
			totals[bucket] += amount
			if show_details:
				children.append(
					{
						"row_id": f"{doctype}:{invoice['name']}",
						"parent_id": doctype,
						"flow": invoice["name"],
						"invoice": invoice["name"],
						"invoice_type": doctype,
						"party_type": "Customer" if doctype == "Sales Invoice" else "Supplier",
						"party": invoice["party"],
						"due_date": invoice.get("due_date"),
						"posting_date": invoice.get("posting_date"),
						"indent": 1,
						"currency": currency,
						bucket: float(amount),
						"total": float(amount),
					}
				)
		parent.update({key: float(value) for key, value in totals.items()})
		parent["total"] = float(sum(totals.values()))
		groups.append((parent, children, totals))
	net = {"row_id": "net", "flow": "Net Expected Cash Flow", "indent": 0, "currency": currency}
	net_totals = {key: sum(group[2][key] for group in groups) for key in keys}
	net.update({key: float(value) for key, value in net_totals.items()})
	net["total"] = float(sum(net_totals.values()))
	return [row for parent, children, _totals in groups for row in [parent, *children]] + [net]
