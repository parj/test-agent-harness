"""
In-run context compaction — triggered by agent/runtime.py when a
conversation's token usage approaches the configured model's context
window. Summarizes the older portion of the conversation into one compact
note and keeps the most recent turns verbatim, so a long-running task or
chat session doesn't blow the context window (see config.context_window
and settings.context_compact_ratio / context_warn_ratio).

Same selective-preservation idea as the nightly usage-profile compaction in
memory/consolidate.py, applied to a single run instead of across days.
"""
import asyncio

COMPACTION_SYSTEM_PROMPT = "You compact conversation history for an ongoing finance-agent session."

COMPACTION_INSTRUCTION = """Summarize the conversation above into a compact note that lets the \
assistant continue seamlessly from here. Keep: any figures/results already retrieved (so they \
don't need to be re-queried), open questions, and decisions made. Drop: verbose tool-call \
payloads and threads that are already fully resolved. Keep it under ~300 words."""


def _find_keep_boundary(messages: list[dict], keep_turns: int) -> int:
    """Index to keep-from. Always lands on a real user turn (role == "user"),
    never role == "tool_result" — so a tool_use/tool_result pair is never
    split between the summarized and kept portions."""
    user_indices = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if len(user_indices) <= keep_turns:
        return 0
    return user_indices[-keep_turns]


async def compact_messages(messages: list[dict], provider, keep_turns: int = 2) -> list[dict]:
    """Returns a shorter message list: a single compacted note covering
    everything before the kept window, followed by the last `keep_turns`
    user turns verbatim. Returns `messages` unchanged if there isn't enough
    history to usefully compact."""
    boundary = _find_keep_boundary(messages, keep_turns)
    if boundary <= 0:
        return messages

    older, recent = messages[:boundary], messages[boundary:]
    response = await asyncio.to_thread(
        provider.complete,
        older + [{"role": "user", "content": COMPACTION_INSTRUCTION}],
        system=COMPACTION_SYSTEM_PROMPT,
    )
    summary = (response.text or "").strip()
    if not summary:
        return messages

    compacted_note = {"role": "user", "content": f"[Earlier conversation, compacted]\n{summary}"}
    return [compacted_note] + recent
