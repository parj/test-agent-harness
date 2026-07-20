"""The full agent loop offline: stub provider → runtime → query_data →
DuckDB origin → ClickHouse cache → summarized answer. Also the approval
gate pausing on expensive queries."""
import pytest

from agent.providers.stub_provider import StubProvider
from agent.runtime import AgentRuntime, ApprovalDecision
from config import settings


@pytest.fixture(autouse=True)
def _stack(manager, cache):
    """All loop tests run against the seeded registry + chdb cache."""
    yield


@pytest.mark.asyncio
async def test_variance_question_end_to_end():
    runtime = AgentRuntime(provider=StubProvider())
    events = []
    text, messages = await runtime.run(
        [{"role": "user", "content": "Explain variance in the ledgers between Q1 and Q2"}],
        on_event=lambda kind, data: events.append(kind),
    )
    assert "Largest movers" in text
    assert "Revenue" in text
    kinds = set(events)
    assert {"llm_call", "tool_start", "tool_result"} <= kinds
    tool_results = [m for m in messages if m["role"] == "tool_result"]
    assert tool_results and "columns" in tool_results[0]["content"]


@pytest.mark.asyncio
async def test_second_run_hits_clickhouse_cache():
    import json
    runtime = AgentRuntime(provider=StubProvider())
    prompt = [{"role": "user", "content": "Explain variance in the ledgers between Q1 and Q2"}]
    await runtime.run(list(prompt))
    _, messages = await runtime.run(list(prompt))
    payload = json.loads([m for m in messages if m["role"] == "tool_result"][-1]["content"])
    assert payload["cache_hit"] is True
    assert payload["served_from"] == "clickhouse-cache"


@pytest.mark.asyncio
async def test_expensive_query_pauses_for_approval(monkeypatch):
    monkeypatch.setattr(settings, "approval_row_threshold", 1000)
    runtime = AgentRuntime(provider=StubProvider())
    seen = {}

    async def handler(request, call):
        seen["rows"] = request.estimated_rows
        seen["sql"] = request.summary
        return ApprovalDecision(approved=False, note="not now")

    text, messages = await runtime.run(
        [{"role": "user", "content": "Pull the AP aging invoices report"}],
        approval_handler=handler,
    )
    assert seen["rows"] > 1000
    assert "ap_invoices" in seen["sql"]
    denial = [m for m in messages if m["role"] == "tool_result"][-1]
    assert denial["content"].startswith("Denied by user")
    assert "not approved" in text


@pytest.mark.asyncio
async def test_approval_modify_swaps_query(monkeypatch):
    import json
    monkeypatch.setattr(settings, "approval_row_threshold", 1000)
    runtime = AgentRuntime(provider=StubProvider())

    async def handler(request, call):
        return ApprovalDecision(
            approved=True,
            modified_input={"sql": "SELECT status, COUNT(*) AS n FROM ap_invoices GROUP BY status"},
        )

    _, messages = await runtime.run(
        [{"role": "user", "content": "Pull the AP aging invoices report"}],
        approval_handler=handler,
    )
    payload = json.loads([m for m in messages if m["role"] == "tool_result"][-1]["content"])
    assert payload["columns"] == ["status", "n"]
    assert payload["row_count"] <= 3


@pytest.mark.asyncio
async def test_approvals_disabled_skips_gate(monkeypatch):
    monkeypatch.setattr(settings, "approval_row_threshold", 1)

    async def handler(request, call):  # must never be called
        raise AssertionError("approval handler called with approvals disabled")

    runtime = AgentRuntime(provider=StubProvider())
    text, _ = await runtime.run(
        [{"role": "user", "content": "How much did we spend on software by vendor?"}],
        approval_handler=handler,
        approvals_enabled=False,
    )
    assert text
