name: Variance Analysis
description: Compare actual spend against budget for one or more accounts and explain what's driving material variances.
tools_required: [query_data]

## Instructions

1. Ask which account(s) and period(s) to analyse, unless already provided (default: all expense accounts, current period).
2. For each account, query actual spend (SUM of transactions) and the budgeted amount for the period.
3. Calculate variance in both £ and % terms.
4. For any account where variance exceeds 5%, pull the individual transactions for that account and period to identify what's driving it.
5. Summarise material variances in a table: account, actual, budget, variance £, variance %, likely driver.
6. Keep commentary factual and grounded in the transaction data — don't speculate beyond what the numbers show.
