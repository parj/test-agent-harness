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
"""


async def init_db():
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(DDL)
    print("Schema ready: sessions, messages, memories.")


if __name__ == "__main__":
    import asyncio
    asyncio.run(init_db())
