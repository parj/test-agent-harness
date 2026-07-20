"""API surface: overview, tasks with the approval lifecycle, chat query,
direct SQL, sources, cache entries, analysis pivot."""
import asyncio

import httpx
import pytest
import pytest_asyncio

from config import settings


@pytest_asyncio.fixture
async def client(manager, cache, monkeypatch):
    import server
    from agent.providers.stub_provider import StubProvider
    monkeypatch.setattr(server, "runtime", server.AgentRuntime(provider=StubProvider()))
    transport = httpx.ASGITransport(app=server.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def _wait_for_status(client, task_id, statuses, timeout=30.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        r = await client.get(f"/api/tasks/{task_id}")
        if r.json()["status"] in statuses:
            return r.json()
        await asyncio.sleep(0.1)
    raise TimeoutError(f"task {task_id} never reached {statuses}")


@pytest.mark.asyncio
async def test_overview(client):
    r = await client.get("/api/overview")
    assert r.status_code == 200
    data = r.json()
    assert data["stats"]["agents_total"] == 6
    assert {s["name"] for s in data["sources"]} == {"finops_erp", "bank_feed"}
    assert "chdb" in data["cache_backend"] or "clickhouse" in data["cache_backend"]


@pytest.mark.asyncio
async def test_task_runs_to_completion(client):
    r = await client.post("/api/tasks", json={
        "description": "Explain variance in the ledgers between Q1 and Q2",
        "agent": "Variance Agent",
        "require_approval": False,
    })
    assert r.status_code == 200
    task = await _wait_for_status(client, r.json()["id"], {"complete", "failed"})
    assert task["status"] == "complete"
    assert any(b["type"] == "table" for b in task["blocks"])
    assert "Largest movers" in task["result_text"]
    assert task["logs"]


@pytest.mark.asyncio
async def test_approval_lifecycle(client, monkeypatch):
    monkeypatch.setattr(settings, "approval_row_threshold", 1000)
    from cache.manager import get_cache
    get_cache().invalidate("finops_erp")

    r = await client.post("/api/tasks", json={
        "description": "Pull the AP aging invoices report",
        "agent": "Report Agent",
        "require_approval": True,
    })
    task_id = r.json()["id"]

    task = await _wait_for_status(client, task_id, {"approval"})
    assert task["approval"]["estimated_rows"] > 1000
    assert "ap_invoices" in task["approval"]["query"]

    # A second decision attempt on a non-waiting task must 409 later;
    # first, approve with a modified (cheaper) query.
    r = await client.post(f"/api/tasks/{task_id}/approval", json={
        "decision": "modify",
        "modified_query": "SELECT status, COUNT(*) AS n FROM ap_invoices GROUP BY status",
    })
    assert r.status_code == 200

    task = await _wait_for_status(client, task_id, {"complete", "failed", "denied"})
    assert task["status"] == "complete"

    r = await client.post(f"/api/tasks/{task_id}/approval", json={"decision": "approve"})
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_denied_task(client, monkeypatch):
    monkeypatch.setattr(settings, "approval_row_threshold", 1000)
    from cache.manager import get_cache
    get_cache().invalidate("finops_erp")

    r = await client.post("/api/tasks", json={
        "description": "Pull the AP aging invoices report",
        "agent": "Report Agent",
        "require_approval": True,
    })
    task_id = r.json()["id"]
    await _wait_for_status(client, task_id, {"approval"})
    await client.post(f"/api/tasks/{task_id}/approval",
                      json={"decision": "deny", "note": "too costly"})
    task = await _wait_for_status(client, task_id, {"denied", "complete", "failed"})
    assert task["status"] == "denied"


@pytest.mark.asyncio
async def test_chat_query_blocks_and_cache(client):
    r = await client.post("/api/query", json={
        "message": "How much did we spend on software by vendor?",
    })
    assert r.status_code == 200
    data = r.json()
    types = [b["type"] for b in data["blocks"]]
    assert "table" in types and "text" in types

    r2 = await client.post("/api/query", json={
        "message": "How much did we spend on software by vendor?",
        "session_id": data["session_id"],
    })
    table = next(b for b in r2.json()["blocks"] if b["type"] == "table")
    assert table["meta"]["cache_hit"] is True


@pytest.mark.asyncio
async def test_direct_sql(client):
    r = await client.post("/api/sql", json={
        "sql": "SELECT quarter, COUNT(*) AS n FROM gl_entries GROUP BY quarter ORDER BY quarter",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["columns"] == ["quarter", "n"]
    assert len(data["rows"]) == 2

    r = await client.post("/api/sql", json={"sql": "DROP TABLE gl_entries"})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_sources_and_refresh(client):
    r = await client.get("/api/sources")
    data = r.json()
    assert data["kinds"] == ["clickhouse", "postgres", "duckdb", "trino"]
    erp = next(s for s in data["sources"] if s["name"] == "finops_erp")
    assert erp["connected"] is True
    assert any(t["name"] == "gl_entries" for t in erp["tables"])

    r = await client.post("/api/sources/finops_erp/refresh")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_analysis_pivot_from_cache(client):
    r = await client.get("/api/analysis/pivot")
    assert r.status_code == 200
    data = r.json()
    accounts = {row["account"] for row in data["rows"]}
    assert any(a.startswith("4100") for a in accounts)
    assert {s["account"] for s in data["summary"]} == {"Gross Profit", "Net Income"}
    assert len(data["trends"]["revenue"]) == 6

    # Second call must be served from the ClickHouse cache.
    r2 = await client.get("/api/analysis/pivot")
    assert r2.json()["cache"]["cache_hit"] is True


@pytest.mark.asyncio
async def test_cache_entries_endpoint(client):
    r = await client.get("/api/cache/entries")
    data = r.json()
    assert data["entries"]
    assert all({"fingerprint", "source", "sql", "fresh"} <= set(e) for e in data["entries"])
