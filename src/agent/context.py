"""
Context manager — assembles what actually goes into each LLM call: base
system instructions + active skill's instructions + relevant long-term
memories + conversation history, with basic token budgeting.
"""
from agent.runtime import SYSTEM_PROMPT
from config import settings
from memory import sessions, semantic
from memory import skills as skills_module


def estimate_tokens(text: str) -> int:
    """Rough ~4-chars-per-token heuristic for budgeting decisions. Swap for a
    real tokenizer (tiktoken or provider-specific) before relying on this in
    production — it's accurate enough to trigger summarisation, not to bill by."""
    return max(1, len(text) // 4)


async def build_system_prompt(user_id: str, user_message: str, skill_name: str | None) -> str:
    parts = [SYSTEM_PROMPT]

    if skill_name:
        skill = skills_module.get_skill(skill_name)
        if skill:
            parts.append(f"\n\n## Active skill: {skill.name}\n{skill.instructions}")

    memories = await semantic.retrieve_relevant(user_id, user_message)
    if memories:
        bullet_list = "\n".join(f"- {m}" for m in memories)
        parts.append(f"\n\nRelevant context from past sessions:\n{bullet_list}")

    return "\n".join(parts)


async def build_conversation(session_id: str, user_message: str) -> list[dict]:
    """Loads persisted history for this session and appends the new user turn.
    If the conversation has grown past the token budget, drops the oldest
    turns rather than blowing the context window."""
    history = await sessions.get_history(session_id)
    history.append({"role": "user", "content": user_message})

    total_tokens = sum(estimate_tokens(str(m.get("content", ""))) for m in history)
    if total_tokens > settings.context_token_limit:
        # v1 strategy: drop the oldest half. A production version should
        # summarise the dropped turns via the LLM instead of discarding them.
        keep_from = len(history) // 2
        history = history[keep_from:]

    return history
