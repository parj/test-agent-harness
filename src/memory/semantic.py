"""
Semantic memory — durable facts extracted from conversations, stored as
embeddings, retrieved by similarity for future sessions. Embeddings always
go through OpenAI regardless of which provider handles chat completions.
"""
import json

from openai import OpenAI

from config import settings
from db.database import get_pool

_embed_client: OpenAI | None = None


def _client() -> OpenAI:
    # Lazy so importing this module never requires OPENAI_API_KEY — only
    # actually embedding does.
    global _embed_client
    if _embed_client is None:
        _embed_client = OpenAI(api_key=settings.embedding_api_key)
    return _embed_client


def embed_text(text: str) -> list[float]:
    response = _client().embeddings.create(model=settings.embedding_model, input=text)
    return response.data[0].embedding


def _to_pgvector(embedding: list[float]) -> str:
    return "[" + ",".join(str(x) for x in embedding) + "]"


async def store_memory(
    user_id: str, content: str, memory_type: str = "fact", source_session_id: str | None = None
):
    embedding = embed_text(content)
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO memories (user_id, content, embedding, memory_type, source_session_id) "
            "VALUES ($1, $2, $3, $4, $5)",
            user_id, content, _to_pgvector(embedding), memory_type, source_session_id,
        )


async def retrieve_relevant(user_id: str, query_text: str, top_k: int | None = None) -> list[str]:
    top_k = top_k or settings.memory_retrieval_count
    query_embedding = embed_text(query_text)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT content FROM memories WHERE user_id = $1 "
            "ORDER BY embedding <=> $2 LIMIT $3",
            user_id, _to_pgvector(query_embedding), top_k,
        )
    return [r["content"] for r in rows]


EXTRACTION_PROMPT = """Review the conversation below. Extract any durable facts worth \
remembering for future sessions — account meanings, team conventions, recurring \
deadlines, stated preferences. Ignore one-off details specific to this single query.
Return a JSON array of short factual strings, or [] if nothing is worth keeping.

Conversation:
{conversation_text}

Respond with ONLY the JSON array, nothing else."""


async def extract_memories_from_conversation(
    conversation_text: str, provider, user_id: str, session_id: str
) -> list[str]:
    """Uses the configured LLM provider to pull durable facts out of a finished
    conversation, then stores each one. Call this after a session ends."""
    response = provider.complete(
        messages=[{"role": "user", "content": EXTRACTION_PROMPT.format(conversation_text=conversation_text)}],
        system="You extract durable facts from conversations. Respond only with a JSON array.",
    )
    try:
        facts = json.loads(response.text or "[]")
    except (json.JSONDecodeError, TypeError):
        return []

    for fact in facts:
        await store_memory(user_id=user_id, content=fact, source_session_id=session_id)
    return facts
