// Copyright (c) 2026, Connect 4 Systems
// For license information, please see license.txt

frappe.query_reports["Cash Flow Forecast"] = {
	tree: true,
	name_field: "row_id",
	parent_field: "parent_id",
	initial_depth: 0,
	filters: [
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
			default: frappe.datetime.add_days(
				frappe.datetime.add_months(frappe.datetime.month_start(), 12), -1
			),
		},
		{
			fieldname: "include_earlier",
			label: __("Include Earlier Due Invoices"),
			fieldtype: "Check",
			default: 1,
		},
		{
			fieldname: "show_details",
			label: __("Show Invoice Details"),
			fieldtype: "Check",
			default: 1,
		},
	],
	formatter(value, row, column, data, default_formatter) {
		const formatted = default_formatter(value, row, column, data);
		return data && !data.indent ? `<strong>${formatted}</strong>` : formatted;
	},
};
