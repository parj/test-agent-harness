"""
Async Postgres connection pool + schema. Requires the pgvector extension —
the pgvector/pgvector:pg16 Docker image ships it, this just enables it.

Run once after `docker compose up -d`:
    python -m db.database
"""
import asyncpg

from config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(settings.postgres_dsn)
    return _pool


DDL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id TEXT NOT NULL,
    skill_name TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    session_id UUID REFERENCES sessions(id),
    role TEXT NOT NULL,               -- user, assistant, tool_result
    content TEXT,
    tool_calls JSONB,
    tool_call_id TEXT,
    tool_name TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memories (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    content TEXT NOT NULL,
    embedding VECTOR(1536),           -- text-embedding-3-small dimensions
    memory_type TEXT DEFAULT 'fact',
    source_session_id UUID,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS memories_embedding_idx
    ON memories USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    agent TEXT NOT NULL,
    sources JSONB NOT NULL DEFAULT '[]',
    reasoning_effort TEXT NOT NULL DEFAULT 'medium',
    require_approval BOOLEAN NOT NULL DEFAULT true,
    status TEXT NOT NULL DEFAULT 'queued',
    creator TEXT DEFAULT '',
    logs JSONB NOT NULL DEFAULT '[]',
    result_text TEXT DEFAULT '',
    blocks JSONB NOT NULL DEFAULT '[]',
    approval JSONB,
    messages JSONB NOT NULL DEFAULT '[]',
    started_at DOUBLE PRECISION,
    duration_ms INTEGER,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    context_pct DOUBLE PRECISION NOT NULL DEFAULT 0,
    trace_id TEXT,
    created_at DOUBLE PRECISION NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS tasks_created_at_idx ON tasks (created_at);

-- CREATE TABLE IF NOT EXISTS above won't add columns to a tasks table that
-- already existed before context_pct/trace_id were introduced — patch them
-- in directly.
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS context_pct DOUBLE PRECISION NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS trace_id TEXT;

-- Usage profiling: raw per-turn activity, folded nightly into a bounded
-- per-user profile so the learning doesn't grow forever (see memory/consolidate.py).
CREATE TABLE IF NOT EXISTS activity_log (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    ts TIMESTAMPTZ DEFAULT now(),
    event_type TEXT NOT NULL,      -- chat_query | task_created | query_data
    summary TEXT NOT NULL,
    agent TEXT,
    source TEXT,
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS activity_log_user_ts_idx ON activity_log (user_id, ts);

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY,
    profile_text TEXT NOT NULL DEFAULT '',
    updated_at TIMESTAMPTZ DEFAULT now(),
    consolidated_through TIMESTAMPTZ
);
"""


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(DDL)
    print("Schema ready: sessions, messages, memories, tasks, activity_log, user_profiles.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(init_db())
