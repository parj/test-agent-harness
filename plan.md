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
 
## Phase 4: Approvals + Audit — ⬜ Not started
 
Designed in the original plan (approval state machine, tiered approval rules, append-only audit log) but no code written yet. `runtime.py` currently has a placeholder — if a tool is marked `requires_approval=True`, it returns a message saying the flow isn't wired up rather than actually pausing and waiting for a human. No tool is currently marked `requires_approval=True` since Phase 3's tools (where the first approval-gated action, `post_journal_entry`, would live) haven't been built yet.
 
## Phase 5: Scheduler + Channels — ⬜ Not started
 
No code written. Original plan calls for APScheduler + Slack/email adapters.
 
## Phase 6: Hardening + Deploy — ⬜ Not started
 
No code written. Error handling, rate limiting, RBAC, deployment — all pending.
 
---
 
## Honest summary
 
**Solid and tested:** the core agent loop, the multi-provider abstraction (Anthropic/OpenAI/Gemini switchable via one env var, verified schema translation for all three), the tool registry pattern, and the skill file system.
 
**Written but unverified:** the entire Postgres/pgvector memory layer. This is real, reasonable code, but "reasonable code I couldn't run" is a meaningfully weaker claim than "tested code" — treat it as a first draft to validate, not a finished feature.
 
**Not started:** everything finance-domain-specific beyond `query_data` (Phase 3), all human-in-the-loop safety (Phase 4), automation (Phase 5), and any frontend at all. The system today is a working CLI proof of concept with a memory layer bolted on that needs its first real test run.
 
**Recommended next step:** get Docker Compose running locally and confirm the Phase 2 database layer actually works end to end before adding more on top of it — building Phase 3 tools on an unverified memory foundation would compound the risk if something in Phase 2 needs to change.
