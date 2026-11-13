### It

IT Business App

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app it
```

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/it
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.


### License

mit

### Cash Flow Forecast

After updating the app, run `bench --site <site> migrate` and `bench --site <site> clear-cache`.
Open **Cash Flow Forecast** (`/app/query-report/Cash%20Flow%20Forecast`), select the company
and date range, then expand Customer Receipts / Refunds or Supplier Payments / Refunds
to inspect invoice links, posting dates, due dates, and expected amounts.

The report groups current outstanding balances of submitted Sales and Purchase Invoices
by **invoice Due Date**, including partial payments and signed credit balances.
It includes invoices posted through today and respects invoice read permissions.
Company currency amounts use the original invoice exchange rate when the party account
is in foreign currency; outstanding already in company currency is not converted again.
Invoices due before From Date have a separate optional column. Missing due dates have a
separate No Due Date column. These columns contribute to Total, not to a forecast month.

This is a current-balance projection, not a historical snapshot or a bank balance forecast.
It does not split payment schedule instalments or include orders and other unbilled commitments.
The standard Cash Flow report continues to show ledger-based financial statements.

Run calculation tests without a Frappe installation:

```bash
python -m unittest it.it.report.cash_flow_forecast.test_forecast
```
