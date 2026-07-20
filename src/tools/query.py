"""
query_data — the agent's window onto the company's data. SELECT-only,
injection-guarded, and cache-aware: results are cached in ClickHouse and
repeat queries are served from there instead of re-hitting the origin
(Postgres / DuckDB / Trino / ClickHouse).

Expensive queries (estimated rows above settings.approval_row_threshold)
raise an ApprovalRequest via the approval_check hook, which the runtime
turns into a human approve/deny/modify step.
"""
from typing import Optional

from pydantic import BaseModel, Field

from config import settings
from datasources.base import ensure_select_only
from tools.base import ApprovalRequest, tool

# Rough per-row scan cost per source kind, used only for the approval UI.
_COST_PER_MILLION_ROWS = {
    "clickhouse": 0.11, "postgres": 0.06, "duckdb": 0.02, "trino": 0.18,
}


class QueryInput(BaseModel):
    sql: str = Field(description="A single SELECT statement to run.")
    source: Optional[str] = Field(
        default=None,
        description="Datasource name to query (defaults to the primary source). "
                    "Use list_sources to see what's connected.",
    )
    refresh: bool = Field(
        default=False,
        description="Set true to bypass the ClickHouse cache and re-pull from the origin.",
    )


def _estimate(params: QueryInput) -> tuple[Optional[int], Optional[float], str]:
    from datasources.registry import get_manager
    manager = get_manager()
    source = manager.get(params.source)
    rows = source.estimate_rows(params.sql)
    cost = None
    if rows is not None:
        per_million = _COST_PER_MILLION_ROWS.get(source.kind, 0.10)
        cost = round(max(rows / 1_000_000 * per_million, 0.001), 3)
    return rows, cost, source.name


def query_approval_check(params: QueryInput) -> Optional[ApprovalRequest]:
    """Gate expensive scans. Cache hits are always free — never gated."""
    from cache.manager import fingerprint, get_cache
    from datasources.registry import get_manager
    manager = get_manager()
    source_name = manager.get(params.source).name
    try:
        sql = ensure_select_only(params.sql)
    except ValueError:
        return None  # will fail validation inside the tool with a clear error
    if not params.refresh:
        cache = get_cache()
        entry = cache._lookup(fingerprint(source_name, sql))
        if entry and cache._is_fresh(entry):
            return None
    rows, cost, source_name = _estimate(params)
    if rows is not None and rows > settings.approval_row_threshold:
        return ApprovalRequest(
            tool="query_data",
            summary=params.sql,
            estimated_rows=rows,
            estimated_cost=cost,
            detail={"source": source_name},
        )
    return None


@tool(
    name="query_data",
    description=(
        "Run a read-only SQL SELECT against a connected datasource. Results are "
        "cached in ClickHouse; repeated queries are served from the cache. "
        "Returns columns, rows (capped), total row count, and cache metadata."
    ),
    approval_check=query_approval_check,
)
async def query_data(params: QueryInput) -> dict:
    import asyncio
    from cache.manager import get_cache

    cache = get_cache()
    cached = await asyncio.to_thread(
        cache.execute, params.source or _default_source(), params.sql,
        force_refresh=params.refresh,
    )
    return {
        "columns": cached.result.columns,
        "rows": cached.result.rows,
        "row_count": cached.total_rows,
        "rows_shown": len(cached.result.rows),
        "truncated": cached.result.truncated,
        "cache_hit": cached.cache_hit,
        "served_from": "clickhouse-cache" if cached.cache_hit else cached.source,
        "cached_as": cached.alias,
        "source": cached.source,
        "elapsed_ms": round(cached.result.elapsed_ms, 1),
    }


def _default_source() -> str:
    from datasources.registry import get_manager
    return get_manager().default_source


class ListSourcesInput(BaseModel):
    pass


@tool(
    name="list_sources",
    description="List connected datasources with kind, default flag, and their tables.",
)
async def list_sources(params: ListSourcesInput) -> dict:
    import asyncio
    from datasources.registry import get_manager

    manager = get_manager()
    out = []
    for name in manager.names():
        source = manager.get(name)
        entry = {"name": name, "kind": source.kind,
                 "is_default": name == manager.default_source}
        try:
            tables = await asyncio.to_thread(source.list_tables)
            entry["tables"] = [
                {"name": t.name, "rows": t.row_count} for t in tables
            ]
        except Exception as e:  # unreachable source still gets listed
            entry["error"] = str(e)
        out.append(entry)
    return {"sources": out}
