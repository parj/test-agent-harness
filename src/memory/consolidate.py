"""
Nightly "compact and sleep" consolidation — folds each user's raw
activity_log rows into a single bounded profile document (user_profiles),
following the same selective-preservation idea as the runtime's
mid-conversation compaction (agent/compaction.py): keep generalizable
patterns, drop one-off case detail, and never let the stored profile grow
without bound.

Runnable standalone for a real cron/systemd timer/docker exec:
    python -m memory.consolidate
It's also driven automatically by the in-process scheduler started in
server.py's startup hook.
"""
import asyncio

from config import settings
from db.database import get_pool

CONSOLIDATION_PROMPT = """You maintain a short internal profile describing how one \
finance-operations user works with this system, so future sessions can be tailored \
to them. Update the profile below using today's raw activity log.

Keep it under ~400 words, organized under these headings:
## Frequent queries
## Preferred sources & agents
## Working patterns
## Notable preferences

Generalize from things that recur; drop one-off details that only happened once and \
aren't likely to matter again. If the existing profile already captures something \
today's activity reconfirms, don't repeat it — just leave it as is. If a section has \
nothing to say yet, omit that heading.

Existing profile:
{profile}

Today's raw activity log:
{activity}

Respond with ONLY the updated profile text, no preamble."""


async def _load_profile(user_id: str) -> tuple[str, object]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT profile_text, consolidated_through FROM user_profiles WHERE user_id = $1",
            user_id,
        )
    if row is None:
        return "", None
    return row["profile_text"] or "", row["consolidated_through"]


async def _load_new_activity(user_id: str, since) -> list:
    pool = await get_pool()
    async with pool.acquire() as conn:
        if since is None:
            rows = await conn.fetch(
                "SELECT ts, event_type, summary, agent, source FROM activity_log "
                "WHERE user_id = $1 ORDER BY ts ASC",
                user_id,
            )
        else:
            rows = await conn.fetch(
                "SELECT ts, event_type, summary, agent, source FROM activity_log "
                "WHERE user_id = $1 AND ts > $2 ORDER BY ts ASC",
                user_id, since,
            )
    return rows


def _format_activity(rows: list) -> str:
    lines = []
    for r in rows:
        bits = [str(r["ts"])[:19], r["event_type"], r["summary"]]
        if r["agent"]:
            bits.append(f"agent={r['agent']}")
        if r["source"]:
            bits.append(f"source={r['source']}")
        lines.append(" | ".join(bits))
    return "\n".join(lines)


async def get_profile_text(user_id: str) -> str:
    """Best-effort profile fetch for injection into the agent system prompt.
    Returns "" on any error (no Postgres, no profile yet) rather than raising —
    a missing profile must never break a chat/task turn."""
    try:
        text, _ = await _load_profile(user_id)
        return text
    except Exception:
        return ""


async def consolidate_user(user_id: str, provider=None) -> str | None:
    """Folds this user's new activity into their profile. Returns the
    updated profile text, or None if there was nothing new to fold in."""
    existing_text, since = await _load_profile(user_id)
    rows = await _load_new_activity(user_id, since)
    if not rows:
        return None

    if provider is None:
        from agent.providers import get_provider
        provider = get_provider()

    prompt = CONSOLIDATION_PROMPT.format(
        profile=existing_text or "(none yet)",
        activity=_format_activity(rows),
    )
    response = await asyncio.to_thread(
        provider.complete,
        messages=[{"role": "user", "content": prompt}],
        system="You write concise, factual internal user-behavior profiles. "
               "Never invent activity that isn't in the log.",
    )
    updated_text = (response.text or existing_text).strip()
    newest_ts = rows[-1]["ts"]

    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO user_profiles (user_id, profile_text, updated_at, consolidated_through)
               VALUES ($1, $2, now(), $3)
               ON CONFLICT (user_id) DO UPDATE SET
                   profile_text = EXCLUDED.profile_text,
                   updated_at = now(),
                   consolidated_through = EXCLUDED.consolidated_through""",
            user_id, updated_text, newest_ts,
        )
        await conn.execute(
            "DELETE FROM activity_log WHERE user_id = $1 AND ts <= $2 "
            "AND ts < now() - make_interval(days => $3)",
            user_id, newest_ts, settings.activity_retention_days,
        )
    return updated_text


async def consolidate_all_users() -> dict[str, str]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT user_id FROM activity_log UNION SELECT user_id FROM user_profiles"
        )
    user_ids = [r["user_id"] for r in rows]

    from agent.providers import get_provider
    provider = get_provider()

    results = {}
    for user_id in user_ids:
        updated = await consolidate_user(user_id, provider=provider)
        if updated is not None:
            results[user_id] = updated
    return results


if __name__ == "__main__":
    outcome = asyncio.run(consolidate_all_users())
    print(f"Consolidated {len(outcome)} user profile(s).")
