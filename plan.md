# FinOps Agent — Progress Plan
 
Tracks actual progress against the original 6-phase plan. "Tested" means verified running in this build session, not just written. "Written, untested" means the code exists and is internally consistent but wasn't run against a live dependency (usually because that dependency — Postgres, a real ERP, a real API key — wasn't available in the build sandbox).
 
---
 
## Phase 1: Walking Skeleton — ✅ Complete
 
| Item | Status |
|---|---|
| Agent runtime (ReAct loop: call LLM → execute tools → repeat) | ✅ Tested |
| Tool registry + `@tool` decorator + schema generation | ✅ Tested |
| `query_data` tool (sandboxed SQL, SELECT-only, injection-guarded) | ✅ Tested — real aggregation query + blocked `DROP TABLE` both verified |
| Sample data seed script (12 accounts, 400 transactions, quarterly budget) | ✅ Tested |
| CLI chat loop (`chat_cli.py`) | ✅ Written — loop logic tested via schema/tool tests; full conversation not run live (needs a real API key, which wasn't in the sandbox) |
 
**Not built:** FastAPI API layer, WebSocket streaming, any frontend. Phase 1 as delivered is CLI-only by design — the plan called for API+frontend here too, but we descoped to prove the core loop first given usage constraints.
 
---
 
## Phase 2: Memory + Skills — 🟡 Partially complete
 
| Item | Status |
|---|---|
| Skill loader (parses markdown files, extracts metadata + instructions) | ✅ Tested — all 3 skill files load and parse correctly |
| 3 skill files: bank reconciliation, variance analysis, daily cash report | ✅ Written — instructions reference `query_data` since that's the only tool that exists so far; will need updating once Phase 3 tools land |
| Context manager (`context.py`): assembles system prompt from skill + memories + history | ✅ Tested — verified with mocked memory retrieval, real skill loading |
| Token estimation + basic truncation strategy | ✅ Written — heuristic only (`len // 4`), not a real tokenizer |
| Postgres schema (sessions, messages, memories tables + pgvector index) | ⬜ Written, untested — no live Postgres in the build sandbox |
| Session persistence (`memory/sessions.py`) | ⬜ Written, untested — same reason |
| Semantic memory: embedding storage + similarity retrieval (`memory/semantic.py`) | ⬜ Written, untested — needs both Postgres and a real OpenAI key |
| Memory extraction from finished conversations | ⬜ Written, untested — depends on the above |
| **Glue: wiring persistence + memory extraction into the actual chat loop** | ❌ Not built — `chat_cli.py` still runs single-turn, in-memory only. Sessions aren't saved, memories aren't extracted automatically. |
| Frontend: session sidebar, skill launcher | ❌ Not built — no frontend exists yet at all |
 
**What "done" looks like for Phase 2:** run `docker compose up -d` + `python -m db.database` locally, confirm schema creates cleanly, then the session/memory code needs a real end-to-end test — start a conversation, close it, start a new one, confirm a fact from the first session shows up in the second. That loop hasn't been run.
 
---
 
## Phase 3: Finance Tools + First Connector — ⬜ Not started
 
Discussed but not yet built. Scope agreed so far:
 
| Item | Status |
|---|---|
| Reconciliation tools (`fetch_ledger_balances`, `fetch_bank_transactions`, `compare_balances`, `generate_rec_report`) | ⬜ Not built |
| Variance tools (`calculate_variance`, `explain_variance`, `generate_variance_commentary`) | ⬜ Not built |
| Close checklist tools (`get_close_checklist`, `update_checklist_step`, `post_journal_entry` — last one needs `requires_approval=True`) | ⬜ Not built |
| Cash flow tools (`get_cash_position`, `forecast_cash_flow`, `flag_liquidity_risk`) | ⬜ Not built |
| Reporting tools (`generate_report`, `send_report`) | ⬜ Not built |
| CSV/Excel ingester (upload real bank statements/budgets) | ⬜ Not built — this is the most tractable next piece since it needs no external account |
| EDMCS connector (cost centre hierarchies — "Saracen") | ⬜ Not built — REST API confirmed to exist; needs an integration user provisioned by your Oracle admin before it can be tested |
| Oracle Fusion connector (GL ledger balances) | ⬜ Not built — REST API confirmed (`ledgerBalances` resource); same credential blocker |
| DB2 warehouse connector (sub-ledger) | ⬜ Not built — most straightforward of the three technically (`ibm_db` driver), same credential blocker |
| Tool execution cards in frontend | ⬜ Not built — no frontend exists yet |
 
**Blocker for the three enterprise connectors specifically:** none of them can be built-and-verified blind. Each needs a real integration user/API credentials from your Oracle/DB2 admin team before there's anything to test against. The CSV ingester has no such blocker and is the natural next thing to build.
 
---
 
## Phase 3.5: FinAgent UI + API + Datasources + ClickHouse Cache — ✅ Complete

Built and tested in this session (all "Tested" claims verified by the 44-test
offline suite plus live browser runs against the running server):

| Item | Status |
|---|---|
| Repo restructured into the documented `src/` package layout (imports now actually resolve) | ✅ Tested |
| Provider adapters: Anthropic / OpenAI / Gemini / **stub** (offline, deterministic — the test-harness path) | ✅ Stub tested end-to-end; real providers written to the shared interface, need API keys to verify live |
| Tool registry (`@tool` + pydantic schemas) and `query_data` (SELECT-only, injection-guarded, cache-aware) | ✅ Tested |
| Datasource connectors: **clickhouse, postgres, duckdb, trino** behind one interface + JSON-config registry | ✅ DuckDB tested live; the other three written to the same interface, need live servers to verify |
| **ClickHouse query cache**: results cached by (source, sql) fingerprint, TTL freshness, stable `<source>__<table>` views, invalidation; backends: clickhouse-connect (server) or chdb (embedded ClickHouse) | ✅ Tested (chdb backend) — miss→hit, TTL expiry, refresh, escaping round-trip, ad-hoc SQL over cached tables |
| Approval gating: estimated-rows threshold → pause → human Approve / Deny / Modify-query → resume | ✅ Tested (runtime + API + UI) |
| FastAPI server: tasks, agents, sources, query chat, direct SQL, analysis pivot, cache entries + WebSocket live events | ✅ Tested |
| FinAgent web UI (faithful port of FinAgent.dc.html): Dashboard, Tasks, Agents, Data Sources, Analysis, Query, New-Task modal | ✅ Tested via Playwright screenshots of every view against the live server |
| Sample data seeding: `finops.duckdb` (~3K GL entries matching the design's quarterly totals, 150K ap_invoices for the approval demo) + `bank_feed.duckdb` | ✅ Tested |
| Offline pytest suite (44 tests: tools, datasources, cache, agent loop, API incl. approval lifecycle) | ✅ Passing |

**Deliberate scope choices:** chat sessions and tasks are in-memory (the
Phase 2 Postgres layer is still not wired in); cost figures on agent cards are
heuristic (rows-scanned based); Snowflake/BigQuery from the design mock were
replaced by the four requested connector kinds.

## Phase 4: Approvals + Audit — 🟡 Approval flow now real

The approval state machine is now implemented (Phase 3.5): `query_data`
declares a per-invocation approval check (estimated rows vs threshold), the
runtime pauses on an async approval handler, and the server/UI complete the
loop with Approve / Deny / Modify. Still missing from the original Phase 4
scope: tiered approval rules and an append-only audit log.
 
## Phase 5: Scheduler + Channels — ⬜ Not started
 
No code written. Original plan calls for APScheduler + Slack/email adapters.
 
## Phase 6: Hardening + Deploy — ⬜ Not started
 
No code written. Error handling, rate limiting, RBAC, deployment — all pending.
 
---
 
## Honest summary
 
**Solid and tested:** the core agent loop (now with a real approval flow), the tool registry, the skill file system, the DuckDB datasource path, the ClickHouse cache on the embedded chdb backend, the FastAPI+WebSocket API, and the FinAgent web UI — all covered by the offline test suite and verified live in a browser.
 
**Written but unverified against live services:** the Anthropic/OpenAI/Gemini adapters (stub verified; real ones need API keys), the Postgres/ClickHouse-server/Trino connectors and the clickhouse-connect cache backend (need live servers), and the entire Postgres/pgvector memory layer. Treat these as first drafts to validate, not finished features.
 
**Not started:** Phase 3's finance-domain tool set beyond `query_data`, tiered approval rules + audit log (rest of Phase 4), automation (Phase 5), hardening/deploy (Phase 6).
 
**Recommended next step:** point the datasource registry at a real Postgres/ClickHouse/Trino and run the suite's connector tests against them, then wire task/session persistence into the Phase 2 memory layer.
