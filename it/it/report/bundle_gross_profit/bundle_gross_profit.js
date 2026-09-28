// Copyright (c) 2026, Connect 4 Systems
// For license information, please see license.txt

frappe.query_reports["Bundle Gross Profit"] = {
	filters: [
		{
			fieldname: "reconcile_cogs",
			label: __("Reconcile COGS"),
			fieldtype: "Check",
			default: 0,
		},
		{
			fieldname: "cogs_account",
			label: __("COGS Account"),
			fieldtype: "Link",
			options: "Account",
			depends_on: "eval:doc.reconcile_cogs",
			get_query: () => ({
				filters: {
					company: frappe.query_report.get_filter_value("company"),
					is_group: 0,
					root_type: "Expense",
				},
			}),
		},
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			reqd: 1,
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.month_start(),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.month_end(),
		},
		{
			fieldname: "customer",
			label: __("Customer"),
			fieldtype: "Link",
			options: "Customer",
		},
		{
			fieldname: "sales_partner",
			label: __("Sales Partner"),
			fieldtype: "Link",
			options: "Sales Partner",
		},
		{
			fieldname: "parent_item",
			label: __("Item"),
			fieldtype: "Link",
			options: "Item",
		},
		{
			fieldname: "sales_invoice",
			label: __("Sales Invoice"),
			fieldtype: "Link",
			options: "Sales Invoice",
		},
		{
			fieldname: "delivery_note",
			label: __("Delivery Note"),
			fieldtype: "Link",
			options: "Delivery Note",
		},
	],
};
