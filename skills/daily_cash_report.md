name: Daily Cash Report
description: Summarise current cash position across accounts and flag anything that needs attention.
tools_required: [query_data]

## Instructions

1. Query the running balance for each asset account (account_type = 'asset') by summing transactions to date.
2. Present each account's balance clearly, and a total across all cash/bank accounts.
3. Compare total cash to the same point last period if data is available, and note the direction of change.
4. If any account balance looks unusually low relative to recent outflows, flag it for attention.
5. Keep the report short — this is meant to be read in under a minute, typically posted to Slack each morning.
