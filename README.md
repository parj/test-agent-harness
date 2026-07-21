# FinAgent — FinOps Agent Harness

A finance-operations AI agent platform — reconciliation, variance analysis, cash
reporting — with a full web UI (the **FinAgent** design), a REST + WebSocket API,
multi-datasource connectors, and a **ClickHouse-backed query cache**.
Provider-agnostic (Claude, GPT, Gemini, or an offline stub for testing), with
markdown-defined skills.

See `plan.md` for what's built, what's tested, and what's left.

---

## Quick start (no external services needed)

```bash
# 1. Python environment
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. Seed sample finance data (two DuckDB files + datasource config)
cd src
python -m db.seed

# 3. Run the server (offline stub provider — no API key required)
PROVIDER=stub python -m uvicorn server:app --port 8720
```

Open http://localhost:8720 — the FinAgent UI: Dashboard, Tasks (with the
approve/deny/modify flow for expensive queries), Agents, Data Sources,
Analysis (pivot straight off the ClickHouse cache), and Query (chat +
structured SQL).

To use a real LLM instead of the stub:

```bash
export PROVIDER=anthropic              # or: openai | gemini
export ANTHROPIC_API_KEY=sk-ant-...    # matching key for the provider
```

If the configured provider has no API key, the harness automatically falls
back to the stub so it always runs.

## Datasources

Four connector kinds are supported: **clickhouse**, **postgres**, **duckdb**,
**trino**. Connected sources are declared in `sample_data/datasources.json`
(created by the seed script) and can also be added live from the UI
(Data Sources → *+ Add Source*) or via `POST /api/sources`:

```json
{
  "default": "finops_erp",
  "sources": [
    {"name": "finops_erp", "kind": "duckdb",     "params": {"path": "finops.duckdb"}},
    {"name": "app_db",     "kind": "postgres",   "params": {"dsn": "postgresql://user:pass@host:5432/db"}},
    {"name": "warehouse",  "kind": "clickhouse", "params": {"host": "ch.internal", "port": 8123, "database": "prod"}},
    {"name": "lakehouse",  "kind": "trino",      "params": {"host": "trino.internal", "port": 8080, "catalog": "hive", "schema": "finance"}}
  ]
}
```

## The ClickHouse cache

Every result the agent pulls from an origin datasource is written into
ClickHouse, keyed by a fingerprint of `(source, sql)`. While the entry is
fresh (`CACHE_TTL_SECONDS`, default 15 min), the same query is served from
ClickHouse without touching the origin. Full-table pulls also get a stable
view (`<source>__<table>`), so follow-up analysis — the pivot view, drill-down
chat questions, ad-hoc SQL — runs against ClickHouse too.

Two interchangeable backends, same SQL semantics:

- **`CLICKHOUSE_URL=http://host:8123` set** → a real ClickHouse server via
  clickhouse-connect.
- **Not set** → [chdb](https://github.com/chdb-io/chdb), the embedded
  in-process ClickHouse engine, persisted under `sample_data/chdb-cache`.
  This is what the test harness uses — no server required.

Queries whose estimated row count exceeds `APPROVAL_ROW_THRESHOLD` (default
100K) pause for human approval when the task requires it; the UI offers
Approve / Deny / Modify-query. Cache hits are never gated.

## API surface

| Route | What it does |
|---|---|
| `GET /api/overview` | dashboard stats, approval queue, feed, cache freshness per source |
| `GET/POST /api/tasks`, `GET /api/tasks/{id}` | task list / create (runs the agent) / detail with logs + result blocks |
| `POST /api/tasks/{id}/approval` | `{"decision": "approve" \| "deny" \| "modify", "modified_query": …}` |
| `POST /api/tasks/{id}/ask` | follow-up question in the task's context |
| `GET /api/agents` | agent roster with live status/progress/cost |
| `GET/POST /api/sources`, `POST /api/sources/{name}/refresh` | list/add sources, invalidate a source's cache |
| `POST /api/query` | natural-language chat → agent → blocks (text/table/chart) |
| `POST /api/sql` | direct SELECT through the cache |
| `GET /api/analysis/pivot` | Q1/Q2 pivot + variance drivers + monthly trends, computed in ClickHouse |
| `GET /api/cache/entries` | raw cache entries with freshness |
| `WS /ws` | live events: feed, task updates, agent logs, approval requests |

## Running the tests

```bash
pytest            # 44 tests: tools, datasources, cache, agent loop, API
```

The suite is fully offline: seeded DuckDB as the origin, chdb as the
ClickHouse cache, stub provider as the LLM.

## Project structure

```
finance-agent-harness/
├── docker-compose.yml        # Postgres + pgvector, for Phase 2 memory
├── requirements.txt
├── sample_data/              # generated: finops.duckdb, bank_feed.duckdb,
│                             #   datasources.json, chdb-cache/
├── skills/                   # markdown workflow definitions, loaded at runtime
├── tests/                    # offline pytest suite
└── src/
    ├── config.py             # all settings, via environment variables
    ├── server.py             # FastAPI app: REST + WS + static UI
    ├── chat_cli.py           # terminal chat loop for testing
    ├── static/               # FinAgent web UI (vanilla JS, no build step)
    ├── agent/
    │   ├── runtime.py        # core reasoning loop + approval gating
    │   ├── context.py        # system prompt assembly: skill + memories
    │   └── providers/        # anthropic / openai / gemini / stub adapters
    ├── tools/
    │   ├── base.py           # @tool decorator, registry, approval checks
    │   └── query.py          # query_data — SELECT-only, cache-aware
    ├── datasources/
    │   ├── base.py           # connector interface + SQL guard
    │   ├── clickhouse_source.py / postgres_source.py /
    │   │   duckdb_source.py / trino_source.py
    │   └── registry.py       # config-driven source manager
    ├── cache/
    │   ├── backends.py       # chdb (embedded) / clickhouse-connect (server)
    │   └── manager.py        # fingerprint → cache table + TTL + views
    ├── memory/               # sessions, semantic memory, skills (Phase 2)
    └── db/
        ├── seed.py           # sample finance data generator
        └── database.py       # Postgres pool + schema DDL (Phase 2)
```

## Environment variables

Everything is configured via environment variables — see `src/config.py`
for the full list. The interesting ones:

| Variable | Default | Purpose |
|---|---|---|
| `PROVIDER` | `anthropic` | `anthropic` \| `openai` \| `gemini` \| `stub` |
| `CLICKHOUSE_URL` | *(empty)* | use a real ClickHouse server for the cache |
| `CHDB_DIR` | `sample_data/chdb-cache` | embedded cache location |
| `CACHE_TTL_SECONDS` | `900` | cache freshness window |
| `APPROVAL_ROW_THRESHOLD` | `100000` | rows above which queries need approval |
| `RESULT_ROW_LIMIT` | `500` | rows returned to the model / UI per query |
| `DATASOURCES_CONFIG` | `sample_data/datasources.json` | source registry file |

## Known limitations (see plan.md for full detail)

- Gemini tool calls use the function name as a stand-in call ID (Gemini
  doesn't return one); fine for one tool call per turn.
- The Postgres/pgvector memory layer (`memory/`) is written but not wired
  into the server — tasks and chat sessions are in-memory.
- Postgres, ClickHouse-as-origin, and Trino connectors are implemented and
  share the tested interface, but only DuckDB (origin) and chdb (cache) are
  exercised live in this sandbox — point real DSNs at them to verify in anger.
- Token counting in `context.py` is a `len(text) // 4` heuristic.
