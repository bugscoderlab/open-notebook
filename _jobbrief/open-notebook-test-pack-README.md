# Open Notebook Team Access - Test Pack

All documents are synthetic. Upload them into notebooks matching their filename and department.

## Suggested notebooks

- Company Shared: 01_company_shared_guide.pdf
- HR: 02_hr_employee_handbook.pdf, 03_hr_recruitment_compensation.pdf
- Finance: 04_finance_monthly_report.pdf, 05_finance_budget_forecast.pdf
- Executive: 06_executive_strategy_memo.pdf

## Canary test

Each document contains a unique canary phrase. Search the forbidden canaries using every role. A correct implementation returns no result, citation, excerpt, autocomplete, or AI answer.

## Analytical chatbot test

Import sales_transactions_2026.csv as a Finance-owned dataset. The expected highest spender is Sarah Lim with MYR 8,460 across 24 completed transactions. Her average completed transaction is MYR 352.50 and her most-used service is Full Groom with 11 transactions.

HR must not receive any customer analytics because the dataset belongs to Finance. Finance, CEO, and Admin may query it. See analytical_chatbot_test_cases.csv for numerical, empty-result, permission, and prompt-injection cases.

See 00_access_control_test_guide.pdf for the complete workflow.
