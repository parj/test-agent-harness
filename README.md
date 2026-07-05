# FinOps Agent
 
A Hermes/OpenClaw-style AI agent for finance operations — reconciliation, variance analysis, cash reporting — built from scratch. Provider-agnostic (Claude, GPT, or Gemini), with persistent memory and markdown-defined skills.
 
See `plan.md` for what's built, what's tested, and what's left.
 
---
 
## Setup
 
```bash
# 1. Python environment
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
 
# 2. Sample finance data (SQLite — used by the query_data tool)
cd src
python -m db.seed
 
# 3. Postgres for memory (only needed for Phase 2 features — skip if you
#    just want the Phase 1 chat loop)
cd ..
docker compose up -d
cd src
python -m db.database    # creates schema, enables pgvector
```
 
## Configuration
 
All config is environment variables (see `src/config.py` for the full list). Minimum to run:
 
```bash
export PROVIDER=anthropic              # or: openai | gemini
export ANTHROPIC_API_KEY=sk-ant-...    # matching key for whichever provider
```
 
For semantic memory (Phase 2), embeddings always go through OpenAI regardless of chat provider:
 
```bash
export OPENAI_API_KEY=sk-...
```
 
For Postgres (Phase 2), defaults assume the Docker Compose service:
 
```bash
export POSTGRES_DSN=postgresql://finops:localdev@localhost:5432/finops
```
 
## Running it
 
```bash
cd src
python chat_cli.py
```
 
Try: *"What did we spend on digital advertising this quarter?"* — the agent writes and runs the SQL itself against the seeded sample data.
 
## Project structure
 
```
finops-agent/
├── docker-compose.yml       # Postgres + pgvector, for Phase 2 memory
├── requirements.txt
├── sample_data/
│   └── finops.db            # seeded SQLite data (chart of accounts, transactions, budget)
├── skills/                  # markdown workflow definitions, loaded at runtime
│   ├── bank_reconciliation.md
│   ├── variance_analysis.md
│   └── daily_cash_report.md
└── src/
    ├── config.py            # all settings, via environment variables
    ├── chat_cli.py           # terminal chat loop for testing
    ├── agent/
    │   ├── runtime.py        # core reasoning loop (provider-agnostic)
    │   ├── context.py        # assembles system prompt: skill + memories + history
    │   └── providers/         # one adapter per LLM backend
    │       ├── base.py
    │       ├── anthropic_provider.py
    │       ├── openai_provider.py
    │       └── gemini_provider.py
    ├── tools/
    │   ├── base.py            # @tool decorator, registry, schema generation
    │   └── query.py           # query_data — sandboxed SQL against finops.db
    ├── memory/
    │   ├── sessions.py         # conversation persistence (Postgres)
    │   ├── semantic.py         # long-term memory via embeddings (pgvector)
    │   └── skills.py           # markdown skill file loader
    └── db/
        ├── seed.py            # generates sample_data/finops.db
        └── database.py        # Postgres pool + schema DDL
```
 
## Switching LLM providers
 
No code changes needed — just environment variables:
 
```bash
export PROVIDER=openai
export OPENAI_API_KEY=sk-...
```
 
```bash
export PROVIDER=gemini
export GEMINI_API_KEY=...
```
 
Each provider translates the same internal tool registry and conversation format into its own wire format — see `agent/providers/base.py` for the shared interface.
 
## Known limitations (see plan.md for full detail)
 
- Gemini tool calls use the function name as a stand-in call ID (Gemini doesn't return one); fine for one tool call per turn, not yet safe for repeated calls to the same tool in a single turn.
- The Postgres/pgvector layer (`memory/sessions.py`, `memory/semantic.py`) is written and internally consistent but not tested against a live database — this sandbox couldn't run Postgres. Verify with `python -m db.database` before trusting it in anger.
- No persistence wiring yet between `chat_cli.py` and the memory layer — sessions aren't actually saved or resumed, and memory extraction isn't triggered automatically. The pieces exist; the glue doesn't yet.
- Token counting in `context.py` is a `len(text) // 4` heuristic, not a real tokenizer. Fine for triggering summarisation, not for billing accuracy.
 