name: Bank Reconciliation
description: Reconcile transactions for a given account and period, surfacing anything that needs review.
tools_required: [query_data]

## Instructions

1. Ask the user which account code and period (YYYY-MM) to reconcile, unless already provided.
2. Query all transactions for that account and period from the `transactions` table, joined to `chart_of_accounts` for the account name.
3. Query the `budget` table for the same account and period to get the expected monthly amount.
4. Sum the actual transactions and compare to budget. Flag if the variance exceeds 10%.
5. List any individual transactions above £3,000 for manual review.
6. Present a short summary: total actual, budget, variance, and any flagged transactions.
