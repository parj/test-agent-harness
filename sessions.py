"""
Conversational memory — sessions and messages persisted to Postgres so a
conversation survives across process restarts and can be resumed later.
"""
import json
import uuid

from db.database import get_pool


async def create_session(user_id: str, skill_name: str | None = None) -> str:
    pool = await get_pool()
    session_id = str(uuid.uuid4())
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO sessions (id, user_id, skill_name) VALUES ($1, $2, $3)",
            session_id, user_id, skill_name,
        )
    return session_id


async def append_message(
    session_id: str,
    role: str,
    content: str | None,
    tool_calls: list[dict] | None = None,
    tool_call_id: str | None = None,
    tool_name: str | None = None,
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO messages (session_id, role, content, tool_calls, tool_call_id, tool_name)
               VALUES ($1, $2, $3, $4, $5, $6)""",
            session_id, role, content,
            json.dumps(tool_calls) if tool_calls else None,
            tool_call_id, tool_name,
        )
        await conn.execute("UPDATE sessions SET updated_at = now() WHERE id = $1", session_id)


async def get_history(session_id: str) -> list[dict]:
    """Returns messages in the internal provider-agnostic format the runtime expects."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT role, content, tool_calls, tool_call_id, tool_name FROM messages "
            "WHERE session_id = $1 ORDER BY id ASC",
            session_id,
        )

    messages = []
    for r in rows:
        if r["role"] == "user":
            messages.append({"role": "user", "content": r["content"]})
        elif r["role"] == "assistant":
            messages.append({
                "role": "assistant",
                "content": r["content"],
                "tool_calls": json.loads(r["tool_calls"]) if r["tool_calls"] else [],
            })
        elif r["role"] == "tool_result":
            messages.append({
                "role": "tool_result",
                "tool_call_id": r["tool_call_id"],
                "name": r["tool_name"],
                "content": r["content"],
            })
    return messages
