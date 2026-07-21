"""
Task persistence — mirrors server.py's in-memory `tasks` dict to Postgres
on every change so tasks survive a server restart, and reloads them back
into memory on startup.
"""
import json

from db.database import get_pool

_JSON_COLUMNS = ("sources", "logs", "blocks", "approval", "messages")

_COLUMNS = [
    "id", "title", "description", "agent", "sources", "reasoning_effort",
    "require_approval", "status", "creator", "logs", "result_text", "blocks",
    "approval", "messages", "started_at", "duration_ms", "input_tokens",
    "output_tokens", "context_pct", "trace_id", "created_at", "updated_at",
]

_UPSERT_SQL = f"""
INSERT INTO tasks ({", ".join(_COLUMNS)})
VALUES ({", ".join(f"${i + 1}" for i in range(len(_COLUMNS)))})
ON CONFLICT (id) DO UPDATE SET
    {", ".join(f"{c} = EXCLUDED.{c}" for c in _COLUMNS if c != "id")}
"""


async def save_task(row: dict):
    pool = await get_pool()
    values = [
        json.dumps(row[c]) if c in _JSON_COLUMNS and row[c] is not None else row[c]
        for c in _COLUMNS
    ]
    async with pool.acquire() as conn:
        await conn.execute(_UPSERT_SQL, *values)


async def load_tasks() -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM tasks ORDER BY created_at ASC")
    tasks = []
    for r in rows:
        d = dict(r)
        for c in _JSON_COLUMNS:
            if isinstance(d.get(c), str):
                d[c] = json.loads(d[c])
        tasks.append(d)
    return tasks
