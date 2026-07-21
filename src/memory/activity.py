"""
Raw usage-activity capture — one row per notable action (a chat question, a
task created, a query executed). This is the input to the nightly
consolidation job (memory/consolidate.py), which folds it into a bounded
per-user profile and prunes the raw rows once they're folded in.
"""
import json

from db.database import get_pool


async def record(
    user_id: str,
    event_type: str,
    summary: str,
    agent: str | None = None,
    source: str | None = None,
    metadata: dict | None = None,
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO activity_log (user_id, event_type, summary, agent, source, metadata)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            user_id, event_type, summary[:2000], agent, source,
            json.dumps(metadata) if metadata else None,
        )
