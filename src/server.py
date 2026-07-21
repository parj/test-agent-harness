"""
FinAgent API server — the bridge between the frontend (src/static) and
the agent harness.

  REST      /api/…    dashboard, tasks, agents, sources, analysis, query
  WebSocket /ws       live events: feed items, task/agent updates, approvals
  Static    /         the FinAgent UI

Tasks run the real AgentRuntime (stub provider by default, a live LLM
when an API key is configured). query_data routes through the ClickHouse
cache, so the Sources view's freshness column and the dashboard cache
strip reflect actual cache state.

Run:  cd src && python -m uvicorn server:app --port 8720
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import observability
import tools  # noqa: F401 — registers query_data / list_sources
from agent.runtime import AgentRuntime, ApprovalDecision, SYSTEM_PROMPT
from cache.manager import get_cache
from config import settings
from datasources.registry import get_manager
from db import tasks_store
from memory import activity as activity_module
from memory import consolidate as consolidate_module
from memory import skills as skills_module
from observability import record_rum_event

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------- #
# Agent roster (mirrors the FinAgent design)
# --------------------------------------------------------------------- #
AGENT_DEFS = [
    {"name": "Recon Agent", "type": "Reconciliation", "icon": "🔍", "bg": "#1a3a2a",
     "skill": "Bank Reconciliation", "sources": ["finops_erp", "bank_feed"]},
    {"name": "Report Agent", "type": "Reporting", "icon": "📊", "bg": "#3a2a1a",
     "skill": None, "sources": ["finops_erp"]},
    {"name": "Cash Agent", "type": "Cash Management", "icon": "💰", "bg": "#1a2a3a",
     "skill": "Daily Cash Report", "sources": ["bank_feed"]},
    {"name": "Close Agent", "type": "Month-End Close", "icon": "📋", "bg": "#2a1a3a",
     "skill": None, "sources": ["finops_erp"]},
    {"name": "Variance Agent", "type": "Variance Analysis", "icon": "📈", "bg": "#3a1a1a",
     "skill": "Variance Analysis", "sources": ["finops_erp"]},
    {"name": "Audit Agent", "type": "Audit & Compliance", "icon": "🛡", "bg": "#1a1a2a",
     "skill": None, "sources": ["finops_erp", "bank_feed"]},
]


# Approximate USD-per-1M-token pricing, keyed by the configured chat provider.
# This is a rough operator-facing estimate, not a billing-accurate figure.
_TOKEN_PRICING = {
    "anthropic": {"input": 3.00, "output": 15.00},
    "openai": {"input": 0.15, "output": 0.60},
    "gemini": {"input": 1.25, "output": 5.00},
    "stub": {"input": 0.0, "output": 0.0},
}


def _estimate_llm_cost(input_tokens: int, output_tokens: int) -> float:
    rates = _TOKEN_PRICING.get(settings.provider, {"input": 0.0, "output": 0.0})
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


@dataclass
class AgentState:
    definition: dict
    status: str = "idle"            # idle | running | waiting
    current_task: Optional[str] = None
    current_task_title: Optional[str] = None
    progress: int = 0
    tasks_completed: int = 0
    cost: float = 0.0

    def to_json(self):
        return {
            **self.definition,
            "status": self.status,
            "current_task": self.current_task,
            "current_task_title": self.current_task_title,
            "progress": self.progress,
            "tasks_completed": self.tasks_completed,
            "cost": round(self.cost, 2),
        }


@dataclass
class Task:
    id: str
    title: str
    description: str
    agent: str
    sources: list[str]
    reasoning_effort: str = "medium"
    require_approval: bool = True
    status: str = "queued"          # queued | running | approval | pending_user | complete | failed | denied
    created_at: float = field(default_factory=time.time)
    creator: str = ""
    logs: list[dict] = field(default_factory=list)
    result_text: str = ""
    blocks: list[dict] = field(default_factory=list)
    approval: Optional[dict] = None
    messages: list[dict] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    duration_ms: Optional[int] = None
    input_tokens: int = 0
    output_tokens: int = 0
    context_pct: float = 0.0
    trace_id: Optional[str] = None
    # Not serialized: set while the runtime is paused waiting on a decision.
    approval_event: Optional[asyncio.Event] = field(default=None, repr=False)
    approval_decision: Optional[dict] = field(default=None, repr=False)

    def to_json(self, with_detail: bool = True):
        base = {
            "id": self.id, "title": self.title, "description": self.description,
            "agent": self.agent, "sources": self.sources, "reasoning_effort": self.reasoning_effort,
            "require_approval": self.require_approval, "status": self.status,
            "created_at": self.created_at, "updated_at": self.updated_at,
            "creator": self.creator, "approval": self.approval,
            "started_at": self.started_at, "duration_ms": self.duration_ms,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "estimated_llm_cost": round(_estimate_llm_cost(self.input_tokens, self.output_tokens), 4),
            "context_pct": round(self.context_pct, 4),
            "trace_id": self.trace_id,
            "trace_url": f"{settings.signoz_url}/trace/{self.trace_id}" if self.trace_id else None,
        }
        if with_detail:
            base["logs"] = self.logs
            base["result_text"] = self.result_text
            base["blocks"] = self.blocks
        return base


class EventBus:
    def __init__(self):
        self.clients: set[asyncio.Queue] = set()
        self.feed: list[dict] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        self.clients.discard(q)

    def publish(self, kind: str, data):
        message = {"type": kind, "data": data, "ts": time.time()}
        for q in list(self.clients):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                self.unsubscribe(q)

    def add_feed(self, icon: str, text: str):
        item = {"ts": time.time(), "icon": icon, "text": text}
        self.feed.insert(0, item)
        del self.feed[50:]
        self.publish("feed", item)


bus = EventBus()
agents: dict[str, AgentState] = {a["name"]: AgentState(a) for a in AGENT_DEFS}
tasks: dict[str, Task] = {}
chat_sessions: dict[str, list[dict]] = {}
runtime = AgentRuntime()
_started_at = time.time()


def _now_hhmm() -> str:
    return dt.datetime.now().strftime("%H:%M")


def _task_row(task: Task) -> dict:
    return {
        "id": task.id, "title": task.title, "description": task.description,
        "agent": task.agent, "sources": task.sources,
        "reasoning_effort": task.reasoning_effort, "require_approval": task.require_approval,
        "status": task.status, "creator": task.creator,
        "logs": task.logs, "result_text": task.result_text, "blocks": task.blocks,
        "approval": task.approval, "messages": task.messages,
        "started_at": task.started_at, "duration_ms": task.duration_ms,
        "input_tokens": task.input_tokens, "output_tokens": task.output_tokens,
        "context_pct": task.context_pct, "trace_id": task.trace_id,
        "created_at": task.created_at, "updated_at": task.updated_at,
    }


def _persist_task(task: Task):
    """Fire-and-forget mirror to Postgres so tasks survive a restart —
    best-effort: a Postgres outage degrades to the old in-memory-only
    behaviour rather than failing the request."""
    async def _save():
        try:
            await tasks_store.save_task(_task_row(task))
        except Exception as e:
            logger.warning("Task persistence failed for %s: %s", task.id, e)
    asyncio.create_task(_save())


def _log(task: Task, text: str, color: str = "#666"):
    entry = {"time": _now_hhmm(), "text": text, "color": color}
    task.logs.append(entry)
    task.updated_at = time.time()
    bus.publish("log", {"task_id": task.id, "entry": entry})
    _persist_task(task)


def _task_changed(task: Task):
    task.updated_at = time.time()
    bus.publish("task", task.to_json())
    _persist_task(task)


def _agents_changed():
    bus.publish("agents", [a.to_json() for a in agents.values()])


# --------------------------------------------------------------------- #
# Task execution
# --------------------------------------------------------------------- #
def _result_blocks_from_messages(messages: list[dict], final_text: str) -> list[dict]:
    """Convert the turn's tool results + final text into UI blocks
    (text / table / bar chart) like the design's query view."""
    blocks: list[dict] = []
    for m in messages:
        if m.get("role") != "tool_result" or m.get("name") != "query_data":
            continue
        try:
            payload = json.loads(m["content"])
        except (json.JSONDecodeError, TypeError):
            continue
        if "columns" not in payload:
            continue
        columns, rows = payload["columns"], payload.get("rows", [])
        blocks.append({
            "type": "table",
            "headers": [str(c).upper() for c in columns],
            "rows": [[_fmt_cell(v) for v in r] for r in rows[:12]],
            "meta": {
                "row_count": payload.get("row_count"),
                "cache_hit": payload.get("cache_hit"),
                "served_from": payload.get("served_from"),
                "cached_as": payload.get("cached_as"),
                "elapsed_ms": payload.get("elapsed_ms"),
            },
        })
        chart = _chart_from_result(columns, rows)
        if chart:
            blocks.append(chart)
    if final_text:
        blocks.append({"type": "text", "content": final_text})
    return blocks


def _fmt_cell(v):
    if isinstance(v, float):
        return f"{v:,.2f}" if abs(v) < 1000 else f"{v:,.0f}"
    return str(v) if v is not None else ""


def _chart_from_result(columns, rows) -> Optional[dict]:
    """label+numeric two-column results become a bar chart block."""
    if len(columns) != 2 or not rows:
        return None
    try:
        values = [(str(r[0]), float(r[1])) for r in rows[:8] if r[1] is not None]
    except (TypeError, ValueError):
        return None
    if not values:
        return None
    top = max(abs(v) for _, v in values) or 1
    return {
        "type": "chart",
        "title": f"{columns[1]} BY {columns[0]}".upper(),
        "bars": [
            {"label": label[:14], "width": f"{abs(v) / top * 100:.0f}%",
             "color": "#ef4444" if v < 0 else "#f59e0b",
             "value": _abbrev(v)}
            for label, v in values
        ],
    }


def _abbrev(v: float) -> str:
    if abs(v) >= 1_000_000:
        return f"{v / 1_000_000:,.1f}M"
    if abs(v) >= 1_000:
        return f"{v / 1_000:,.0f}K"
    return f"{v:,.0f}"


async def _system_prompt_for(task_agent: str) -> str:
    definition = agents[task_agent].definition if task_agent in agents else None
    parts = [SYSTEM_PROMPT]
    manager = get_manager()
    parts.append(
        "\n\nConnected datasources: " + ", ".join(
            f"{n} ({manager.get(n).kind})" for n in manager.names()
        ) + f". Default: {manager.default_source}."
    )
    if definition and definition.get("skill"):
        skill = skills_module.get_skill(definition["skill"])
        if skill:
            parts.append(f"\n\n## Active skill: {skill.name}\n{skill.instructions}")
    try:
        profile_text = await consolidate_module.get_profile_text(settings.demo_user)
    except Exception:
        profile_text = ""
    if profile_text:
        parts.append(f"\n\n## What we know about this user's habits\n{profile_text}")
    return "\n".join(parts)


def _record_activity(event_type: str, summary: str, agent: str | None = None,
                      source: str | None = None, metadata: dict | None = None):
    """Fire-and-forget usage-activity capture — feeds the nightly profile
    consolidation job. Best-effort: a Postgres outage must never break the
    request this is attached to."""
    async def _save():
        try:
            await activity_module.record(settings.demo_user, event_type, summary,
                                         agent=agent, source=source, metadata=metadata)
        except Exception as e:
            logger.warning("Activity logging failed: %s", e)
    asyncio.create_task(_save())


async def _run_task(task: Task):
    agent = agents.get(task.agent)
    if agent:
        agent.status = "running"
        agent.current_task = task.id
        agent.current_task_title = task.title
        agent.progress = 10
        _agents_changed()

    task.status = "running"
    task.started_at = time.time()
    _log(task, f"Task started — agent: {task.agent}", "#666")
    _log(task, f"Datasources: {', '.join(task.sources) or 'default'}", "#666")
    _task_changed(task)
    bus.add_feed("▶", f"{task.agent} started: {task.title}")

    def on_event(kind: str, data: dict):
        if agent:
            agent.progress = min(90, agent.progress + 18)
        if kind == "llm_call":
            task.input_tokens += data.get("input_tokens", 0)
            task.output_tokens += data.get("output_tokens", 0)
            _log(task, f"LLM call ({data['provider']}, iteration {data['iteration']})", "#666")
            _task_changed(task)
        elif kind == "tool_start":
            sql = (data.get("input") or {}).get("sql", "")
            _log(task, f"▶ query_data: {sql[:90]}", "#22c55e")
            _record_activity("query_data", sql, agent=task.agent,
                             source=(data.get("input") or {}).get("source"))
        elif kind == "context_usage":
            task.context_pct = data["pct"]
            if data["pct"] >= settings.context_warn_ratio:
                _log(task, f"⚠ Context {data['pct'] * 100:.0f}% full — will auto-compact soon",
                     "#f59e0b")
            _task_changed(task)
        elif kind == "context_compacted":
            _log(task, "↻ Conversation compacted to stay within the model's context window",
                 "#3b9eff")
        elif kind == "tool_result":
            try:
                payload = json.loads(data.get("result", "{}"))
                if "row_count" in payload:
                    origin = ("ClickHouse cache" if payload.get("cache_hit")
                              else payload.get("source", "origin"))
                    _log(task, f"{payload['row_count']:,} rows from {origin}", "#666")
                    if not payload.get("cache_hit"):
                        bus.add_feed("✓", f"Cache refreshed: {payload.get('cached_as') or payload.get('source')}")
                    if agent:
                        agent.cost += (payload.get("row_count", 0) / 1_000_000) * 0.05 + 0.005
                    if observability.cache_result_counter is not None:
                        observability.cache_result_counter.add(
                            1, {"result": "hit" if payload.get("cache_hit") else "miss"})
            except (json.JSONDecodeError, TypeError):
                pass
        elif kind == "approval_needed":
            _log(task, "⚠ Cost threshold exceeded, requesting approval", "#f59e0b")
            bus.add_feed("⏸", f"{task.agent} requests approval: {task.title}")
        elif kind == "approval_granted":
            _log(task, "✓ Approved — running query", "#22c55e")
        elif kind == "approval_denied":
            _log(task, "✕ Denied by user", "#ef4444")
        _agents_changed()

    async def approval_handler(request, call) -> ApprovalDecision:
        task.status = "approval"
        task.approval = {
            "tool": request.tool,
            "query": request.summary,
            "estimated_rows": request.estimated_rows,
            "estimated_cost": request.estimated_cost,
            "source": (request.detail or {}).get("source"),
            "requested_at": time.time(),
        }
        if agent:
            agent.status = "waiting"
            _agents_changed()
        _task_changed(task)
        bus.publish("approval", {"task": task.to_json(with_detail=False)})

        task.approval_decision = None
        task.approval_event = asyncio.Event()
        try:
            await asyncio.wait_for(task.approval_event.wait(),
                                   settings.approval_timeout_seconds)
        except asyncio.TimeoutError:
            task.approval_decision = {"approved": False, "note": "approval timed out"}
        decision = task.approval_decision or {"approved": False, "note": "no decision"}
        task.approval_event = None
        task.status = "running"
        if agent:
            agent.status = "running"
            _agents_changed()
        _task_changed(task)
        return ApprovalDecision(
            approved=decision.get("approved", False),
            modified_input=decision.get("modified_input"),
            note=decision.get("note", ""),
        )

    try:
        conversation = task.messages or [{"role": "user", "content": task.description}]
        with observability.tracer.start_as_current_span("agent.task") as span:
            span.set_attribute("task.id", task.id)
            span.set_attribute("task.agent", task.agent)
            span.set_attribute("task.title", task.title)
            span_ctx = span.get_span_context()
            if span_ctx.is_valid:
                task.trace_id = format(span_ctx.trace_id, "032x")
                _task_changed(task)
            final_text, messages = await runtime.run(
                conversation,
                on_event=on_event,
                system_prompt=await _system_prompt_for(task.agent),
                approval_handler=approval_handler,
                approvals_enabled=task.require_approval,
                reasoning_effort=task.reasoning_effort,
            )
        new_messages = messages[len(conversation):]
        task.messages = messages
        task.result_text = final_text
        task.blocks += _result_blocks_from_messages(new_messages, final_text)
        denied = any(
            m.get("role") == "tool_result" and str(m.get("content", "")).startswith("Denied by user")
            for m in messages
        )
        tool_error = next(
            (str(m.get("content", "")) for m in messages
             if m.get("role") == "tool_result" and str(m.get("content", "")).startswith("Tool error:")),
            None,
        )
        if denied:
            task.status = "denied"
        elif tool_error is not None:
            task.status = "failed"
        else:
            # Not "complete" yet — the agent may still get a follow-up
            # question from the user. _watch_pending_timeout auto-completes
            # it after a period of inactivity (see config.task_followup_idle_seconds).
            task.status = "pending_user"
        _status_log = {
            "pending_user": "⏸ Awaiting your reply",
            "denied": "✕ Ended after denial",
            "failed": f"✕ Failed: {tool_error}",
        }
        _status_color = {"pending_user": "#a78bfa", "denied": "#ef4444", "failed": "#ef4444"}
        _status_icon = {"pending_user": "⏸", "denied": "✕", "failed": "✕"}
        _log(task, _status_log[task.status], _status_color[task.status])
        bus.add_feed(_status_icon[task.status], f"{task.agent} finished: {task.title}")
    except Exception as e:
        task.status = "failed"
        _log(task, f"✕ Failed: {e}", "#ef4444")
        bus.add_feed("✕", f"{task.agent} failed: {task.title} — {e}")
    finally:
        if task.started_at is not None:
            task.duration_ms = int((time.time() - task.started_at) * 1000)
            if observability.task_duration_ms is not None:
                observability.task_duration_ms.record(task.duration_ms, {"agent": task.agent})
        if observability.task_counter is not None:
            observability.task_counter.add(1, {"agent": task.agent, "status": task.status})
        if agent:
            agent.status = "idle"
            agent.current_task = None
            agent.current_task_title = None
            agent.progress = 0
            _agents_changed()
        _task_changed(task)
        if task.status == "pending_user":
            asyncio.create_task(_watch_pending_timeout(task, task.updated_at))


async def _watch_pending_timeout(task: Task, marker: float, delay: float | None = None):
    """Auto-completes a task that's been sitting in pending_user with no
    follow-up. `marker` pins this watcher to the specific pending_user
    transition it was scheduled for — if a follow-up came in (and moved
    updated_at) before the timer fires, this is a stale watcher and no-ops,
    the newer follow-up's own run will schedule its own watcher."""
    await asyncio.sleep(settings.task_followup_idle_seconds if delay is None else max(0.0, delay))
    if task.status != "pending_user" or task.updated_at != marker:
        return
    task.status = "complete"
    agent = agents.get(task.agent)
    if agent:
        agent.tasks_completed += 1
        _agents_changed()
    if observability.task_counter is not None:
        observability.task_counter.add(1, {"agent": task.agent, "status": task.status})
    _log(task, "✓ Marked complete — no follow-up in the last hour", "#3b9eff")
    bus.add_feed("✓", f"{task.agent} auto-completed: {task.title}")
    _task_changed(task)


# --------------------------------------------------------------------- #
# API models
# --------------------------------------------------------------------- #
class CreateTaskBody(BaseModel):
    description: str
    agent: str = "Recon Agent"
    sources: list[str] = []
    reasoning_effort: str = "medium"
    require_approval: bool = True
    title: Optional[str] = None


class ApprovalBody(BaseModel):
    decision: str                    # approve | deny | modify
    modified_query: Optional[str] = None
    note: str = ""


class QueryBody(BaseModel):
    message: str
    session_id: Optional[str] = None


class SqlBody(BaseModel):
    sql: str
    source: Optional[str] = None
    refresh: bool = False


class AddSourceBody(BaseModel):
    name: str
    kind: str
    params: dict
    icon: Optional[str] = None


class AskTaskBody(BaseModel):
    message: str


class RumBody(BaseModel):
    events: list[dict]


# --------------------------------------------------------------------- #
app = FastAPI(title="FinAgent")
observability.setup_telemetry(app)


@app.on_event("startup")
async def _load_persisted_tasks():
    try:
        from db.database import init_db
        await init_db()
        rows = await tasks_store.load_tasks()
    except Exception as e:
        logger.warning("Task persistence unavailable (Postgres not reachable?): %s", e)
        return
    for row in rows:
        task = Task(
            id=row["id"], title=row["title"], description=row["description"],
            agent=row["agent"], sources=row["sources"] or [],
            reasoning_effort=row["reasoning_effort"], require_approval=row["require_approval"],
            status=row["status"], created_at=row["created_at"], creator=row["creator"] or "",
            logs=row["logs"] or [], result_text=row["result_text"] or "",
            blocks=row["blocks"] or [], approval=row["approval"], messages=row["messages"] or [],
            updated_at=row["updated_at"], started_at=row["started_at"], duration_ms=row["duration_ms"],
            input_tokens=row["input_tokens"] or 0, output_tokens=row["output_tokens"] or 0,
            context_pct=row.get("context_pct") or 0.0, trace_id=row.get("trace_id"),
        )
        if task.status in ("queued", "running", "approval"):
            task.status = "failed"
            task.approval = None
            _log(task, "✕ Interrupted — server restarted before this task finished", "#ef4444")
        elif task.status == "pending_user":
            remaining = settings.task_followup_idle_seconds - (time.time() - task.updated_at)
            asyncio.create_task(_watch_pending_timeout(task, task.updated_at, remaining))
        tasks[task.id] = task
    logger.info("Loaded %d persisted task(s) from Postgres.", len(rows))


async def _nightly_consolidation_loop():
    """Runs memory.consolidate.consolidate_all_users() once a day at
    settings.consolidation_hour_utc — the 'compact and sleep' step that
    folds raw activity_log rows into each user's bounded profile. Runs for
    the lifetime of the server process; a Postgres/LLM outage on any given
    night is logged and simply retried the following night."""
    while True:
        now = dt.datetime.utcnow()
        next_run = now.replace(hour=settings.consolidation_hour_utc, minute=0,
                               second=0, microsecond=0)
        if next_run <= now:
            next_run += dt.timedelta(days=1)
        await asyncio.sleep((next_run - now).total_seconds())
        try:
            results = await consolidate_module.consolidate_all_users()
            bus.add_feed("🌙", f"Nightly consolidation: updated {len(results)} user profile(s)")
        except Exception as e:
            logger.warning("Nightly consolidation failed: %s", e)


@app.on_event("startup")
async def _start_nightly_scheduler():
    asyncio.create_task(_nightly_consolidation_loop())


@app.get("/api/overview")
async def overview():
    cache = get_cache()
    rollup = await asyncio.to_thread(cache.source_rollup)
    manager = get_manager()
    running = sum(1 for a in agents.values() if a.status == "running")
    waiting = sum(1 for a in agents.values() if a.status == "waiting")
    idle = len(agents) - running - waiting
    pending = [t for t in tasks.values() if t.status in ("queued", "running", "approval")]
    approvals = [t.to_json(with_detail=False) for t in tasks.values() if t.status == "approval"]
    newest_age = min(
        (r["newest_age"] for r in rollup.values() if r["newest_age"] is not None),
        default=None,
    )
    sources = []
    for entry in manager.describe_all():
        r = rollup.get(entry["name"])
        sources.append({
            **entry,
            "cache": r if r else None,
        })
    return {
        "stats": {
            "agents_total": len(agents), "agents_running": running,
            "agents_waiting": waiting, "agents_idle": idle,
            "tasks_pending": len(pending),
            "tasks_need_approval": len(approvals),
            "cache_newest_age": newest_age,
            "queries_today": cache.stats.queries_today,
            "cache_hits": cache.stats.hits,
            "cache_misses": cache.stats.misses,
        },
        "approvals": approvals,
        "feed": bus.feed[:20],
        "sources": sources,
        "user": settings.demo_user,
        "cache_backend": cache.backend.label,
    }


@app.get("/api/tasks")
async def list_tasks():
    ordered = sorted(tasks.values(), key=lambda t: t.created_at, reverse=True)
    return {"tasks": [t.to_json(with_detail=False) for t in ordered]}


@app.post("/api/tasks")
async def create_task(body: CreateTaskBody):
    if body.agent not in agents:
        raise HTTPException(400, f"Unknown agent: {body.agent}")
    task = Task(
        id=uuid.uuid4().hex[:12],
        title=body.title or (body.description[:60] + ("…" if len(body.description) > 60 else "")),
        description=body.description,
        agent=body.agent,
        sources=body.sources,
        reasoning_effort=body.reasoning_effort,
        require_approval=body.require_approval,
        creator=settings.demo_user,
    )
    tasks[task.id] = task
    _task_changed(task)
    _record_activity("task_created", body.description, agent=body.agent,
                     source=(body.sources[0] if body.sources else None))
    asyncio.create_task(_run_task(task))
    return task.to_json()


@app.get("/api/tasks/{task_id}")
async def get_task(task_id: str):
    if task_id not in tasks:
        raise HTTPException(404, "No such task")
    return tasks[task_id].to_json()


@app.post("/api/tasks/{task_id}/approval")
async def decide_approval(task_id: str, body: ApprovalBody):
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(404, "No such task")
    if task.status != "approval" or task.approval_event is None:
        raise HTTPException(409, "Task is not waiting for approval")

    if body.decision == "approve":
        decision = {"approved": True, "note": body.note}
    elif body.decision == "modify":
        if not body.modified_query:
            raise HTTPException(400, "modified_query required for modify")
        decision = {
            "approved": True,
            "modified_input": {"sql": body.modified_query,
                               "source": task.approval.get("source")},
            "note": body.note,
        }
    elif body.decision == "deny":
        decision = {"approved": False, "note": body.note or "denied from UI"}
    else:
        raise HTTPException(400, f"Unknown decision: {body.decision}")

    task.approval_decision = decision
    task.approval_event.set()
    return {"ok": True}


@app.post("/api/tasks/{task_id}/ask")
async def ask_task(task_id: str, body: AskTaskBody):
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(404, "No such task")
    if task.status in ("queued", "running", "approval"):
        raise HTTPException(409, "Task is still running")
    task.messages.append({"role": "user", "content": body.message})
    asyncio.create_task(_run_task(task))
    return {"ok": True}


@app.get("/api/agents")
async def list_agents():
    return {"agents": [a.to_json() for a in agents.values()]}


@app.get("/api/profile")
async def get_profile():
    """The learned usage profile for the current demo user — built up by
    the nightly consolidation job from activity_log (see memory/consolidate.py).
    Best-effort: returns an empty profile rather than failing if Postgres or
    the profile isn't available yet."""
    try:
        from db.database import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT profile_text, updated_at FROM user_profiles WHERE user_id = $1",
                settings.demo_user,
            )
    except Exception as e:
        return {"user": settings.demo_user, "profile_text": "", "updated_at": None,
                "error": f"Profile unavailable: {e}"}
    if row is None:
        return {"user": settings.demo_user, "profile_text": "", "updated_at": None}
    return {"user": settings.demo_user, "profile_text": row["profile_text"],
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None}


@app.get("/api/sources")
async def list_sources_api():
    manager = get_manager()
    cache = get_cache()
    rollup = await asyncio.to_thread(cache.source_rollup)

    async def status_of(name):
        source = manager.get(name)
        return await asyncio.to_thread(source.ping)

    names = manager.names()
    statuses = await asyncio.gather(*(status_of(n) for n in names))
    out = []
    for name, status in zip(names, statuses):
        entry = manager.describe_all()[names.index(name)]
        r = rollup.get(name)
        tables = None
        if status.connected:
            try:
                infos = await asyncio.to_thread(manager.get(name).list_tables)
                tables = [{"name": t.name, "rows": t.row_count} for t in infos]
            except Exception:
                tables = None
        out.append({
            **entry,
            "connected": status.connected,
            "error": status.error,
            "cache": r,
            "tables": tables,
        })
    return {"sources": out, "kinds": ["clickhouse", "postgres", "duckdb", "trino"],
            "default": manager.default_source}


@app.post("/api/sources")
async def add_source(body: AddSourceBody):
    manager = get_manager()
    if body.name in manager.names():
        raise HTTPException(409, f"Source {body.name} already exists")
    entry = {"name": body.name, "kind": body.kind, "params": body.params}
    if body.icon:
        entry["icon"] = body.icon
    try:
        source = manager.add_source(entry)
    except ValueError as e:
        raise HTTPException(400, str(e))
    status = await asyncio.to_thread(source.ping)
    bus.add_feed("◉", f"Datasource added: {body.name} ({body.kind})")
    return {**source.describe(), "connected": status.connected, "error": status.error}


@app.post("/api/sources/{name}/refresh")
async def refresh_source(name: str):
    manager = get_manager()
    if name not in manager.names():
        raise HTTPException(404, "No such source")
    cache = get_cache()
    removed = await asyncio.to_thread(cache.invalidate, name)
    bus.add_feed("↻", f"Cache invalidated for {name} ({removed} entries) — next queries re-pull")
    return {"invalidated": removed}


@app.get("/api/cache/entries")
async def cache_entries():
    cache = get_cache()
    return {
        "backend": cache.backend.label,
        "database": cache.db,
        "ttl_seconds": cache.ttl,
        "entries": await asyncio.to_thread(cache.entries),
        "stats": {
            "hits": cache.stats.hits, "misses": cache.stats.misses,
            "queries_today": cache.stats.queries_today,
        },
    }


@app.post("/api/query")
async def query_chat(body: QueryBody):
    session_id = body.session_id or uuid.uuid4().hex[:12]
    conversation = chat_sessions.setdefault(session_id, [])
    conversation.append({"role": "user", "content": body.message})
    _record_activity("chat_query", body.message)

    context_state = {"pct": 0.0, "compacted": False}

    def on_event(kind: str, data: dict):
        if kind == "context_usage":
            context_state["pct"] = data["pct"]
        elif kind == "context_compacted":
            context_state["compacted"] = True

    with observability.tracer.start_as_current_span("agent.chat") as span:
        span.set_attribute("chat.session_id", session_id)
        final_text, messages = await runtime.run(
            list(conversation),
            on_event=on_event,
            system_prompt=await _system_prompt_for("Variance Agent"),
            approvals_enabled=False,   # interactive chat: user is already present
        )
    chat_sessions[session_id] = messages
    blocks = _result_blocks_from_messages(messages[len(conversation):], final_text)
    return {
        "session_id": session_id, "blocks": blocks,
        "context_pct": round(context_state["pct"], 4),
        "context_warning": context_state["pct"] >= settings.context_warn_ratio,
        "compacted": context_state["compacted"],
    }


@app.post("/api/sql")
async def run_sql(body: SqlBody):
    cache = get_cache()
    manager = get_manager()
    source = body.source or manager.default_source
    try:
        cached = await asyncio.to_thread(
            cache.execute, source, body.sql, force_refresh=body.refresh,
        )
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e))
    return {
        "columns": cached.result.columns,
        "rows": cached.result.rows,
        "row_count": cached.total_rows,
        "cache_hit": cached.cache_hit,
        "served_from": "clickhouse-cache" if cached.cache_hit else cached.source,
        "cached_as": cached.alias,
    }


@app.get("/api/analysis/pivot")
async def analysis_pivot(source: str = "finops_erp", table: str = "gl_entries"):
    """Pivot straight off the ClickHouse cache — the origin is only hit
    when the table isn't cached (or is stale)."""
    cache = get_cache()
    cached = await asyncio.to_thread(cache.ensure_cached_table, source, table)
    alias = cached.alias
    db = cache.db

    pivot = await asyncio.to_thread(
        cache.query_cached,
        f"SELECT account_code, account_name, "
        f"round(sumIf(amount, quarter = 'Q1')) AS q1, "
        f"round(sumIf(amount, quarter = 'Q2')) AS q2 "
        f"FROM {db}.`{alias}` GROUP BY account_code, account_name ORDER BY account_code",
    )
    drivers = await asyncio.to_thread(
        cache.query_cached,
        f"SELECT vendor, "
        f"round(sumIf(amount, quarter = 'Q2') - sumIf(amount, quarter = 'Q1')) AS delta "
        f"FROM {db}.`{alias}` GROUP BY vendor ORDER BY abs(delta) DESC LIMIT 8",
    )
    monthly = await asyncio.to_thread(
        cache.query_cached,
        f"SELECT toStartOfMonth(entry_date) AS m, "
        f"round(sumIf(amount, account_code LIKE '4%')) AS revenue, "
        f"round(sumIf(amount, account_code = '5100')) AS cogs, "
        f"round(sumIf(amount, account_code LIKE '4%') "
        f"      - sumIf(amount, account_code NOT LIKE '4%')) AS net "
        f"FROM {db}.`{alias}` GROUP BY m ORDER BY m",
    )

    rows = []
    revenue = {"q1": 0.0, "q2": 0.0}
    cogs = {"q1": 0.0, "q2": 0.0}
    total = {"q1": 0.0, "q2": 0.0}
    for code, name, q1, q2 in pivot.rows:
        q1, q2 = float(q1 or 0), float(q2 or 0)
        rows.append({"code": code, "account": f"{code} · {name}", "q1": q1, "q2": q2})
        if str(code).startswith("4"):
            revenue["q1"] += q1; revenue["q2"] += q2
            total["q1"] += q1; total["q2"] += q2
        else:
            total["q1"] -= q1; total["q2"] -= q2
            if str(code) == "5100":
                cogs["q1"] += q1; cogs["q2"] += q2

    summary_rows = [
        {"account": "Gross Profit", "bold": True,
         "q1": revenue["q1"] - cogs["q1"], "q2": revenue["q2"] - cogs["q2"]},
        {"account": "Net Income", "bold": True, "q1": total["q1"], "q2": total["q2"]},
    ]
    trends = {
        "labels": [str(m)[:7] for m, *_ in monthly.rows],
        "revenue": [float(r or 0) for _, r, _c, _n in monthly.rows],
        "cogs": [float(c or 0) for _, _r, c, _n in monthly.rows],
        "net": [float(n or 0) for _, _r, _c, n in monthly.rows],
    }
    return {
        "rows": rows,
        "summary": summary_rows,
        "drivers": [{"vendor": v, "delta": float(d or 0)} for v, d in drivers.rows],
        "trends": trends,
        "cache": {
            "cache_hit": cached.cache_hit,
            "table": alias,
            "row_count": cached.total_rows,
            "backend": cache.backend.label,
        },
    }


@app.post("/api/rum")
async def rum_ingest(body: RumBody):
    """Receives batched client-side RUM events (clicks, page-load timing,
    per-SPA-view duration — see static/rum.js) and turns each into a span +
    metric point on the finagent-web OTel service. Fire-and-forget from the
    browser's perspective: this always returns 202 even if telemetry export
    itself later fails in the background."""
    for event in body.events[:200]:
        try:
            record_rum_event(event)
        except Exception as e:
            logger.warning("RUM event dropped: %s", e)
    return {"accepted": len(body.events)}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    q = bus.subscribe()
    try:
        await ws.send_json({"type": "hello", "data": {
            "feed": bus.feed[:20],
            "agents": [a.to_json() for a in agents.values()],
        }, "ts": time.time()})
        while True:
            message = await q.get()
            await ws.send_json(message)
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        bus.unsubscribe(q)


# Static frontend (mounted last so /api and /ws win).
import os
_static_dir = os.path.join(os.path.dirname(__file__), "static")


@app.get("/")
async def index():
    return FileResponse(os.path.join(_static_dir, "index.html"))


app.mount("/", StaticFiles(directory=_static_dir), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.server_host, port=settings.server_port)
