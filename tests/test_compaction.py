"""Context-window compaction: a conversation whose token usage crosses the
configured compact ratio should trigger agent/compaction.py mid-run,
shrinking the message list before the loop continues."""
import pytest

from agent.providers.stub_provider import StubProvider
from agent.runtime import AgentRuntime
from config import settings


@pytest.fixture(autouse=True)
def _stack(manager, cache):
    yield


def _padded_turn(n: int) -> list[dict]:
    return [
        {"role": "user", "content": f"Turn {n}: " + ("filler " * 40)},
        {"role": "assistant", "content": f"Ack turn {n}: " + ("filler " * 40)},
    ]


@pytest.mark.asyncio
async def test_long_conversation_triggers_compaction(monkeypatch):
    monkeypatch.setattr(settings, "stub_context_window", 200)  # tiny window, easy to exceed

    conversation = []
    for n in range(4):
        conversation += _padded_turn(n)
    conversation.append({"role": "user", "content": "How much did we spend on software by vendor?"})

    runtime = AgentRuntime(provider=StubProvider())
    events = []
    text, messages = await runtime.run(
        conversation,
        on_event=lambda kind, data: events.append((kind, data)),
    )

    assert any(kind == "context_usage" for kind, _ in events)
    assert any(kind == "context_compacted" for kind, _ in events)
    assert any("[Earlier conversation, compacted]" in str(m.get("content", "")) for m in messages)
    assert text


@pytest.mark.asyncio
async def test_short_conversation_does_not_compact():
    runtime = AgentRuntime(provider=StubProvider())
    events = []
    await runtime.run(
        [{"role": "user", "content": "How much did we spend on software by vendor?"}],
        on_event=lambda kind, data: events.append(kind),
    )
    assert "context_compacted" not in events
