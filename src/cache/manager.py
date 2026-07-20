"""
ClickHouse-backed query cache.

Every result the agent pulls from an origin datasource (Postgres, DuckDB,
Trino, or another ClickHouse) is written into ClickHouse as a table, keyed
by a fingerprint of (source, sql). While the entry is fresh (TTL), any
repeat of the same query is served straight from ClickHouse without
touching the origin. Full-table pulls also get a stable view named
"<source>__<table>" so follow-up analysis (pivots, drill-downs, ad-hoc
SQL) runs against ClickHouse too.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
import threading
import time
from dataclasses import dataclass, field

from config import settings
from datasources.base import (
    QueryResult, ensure_select_only, infer_types_from_rows,
)
from cache.backends import CacheBackend, make_backend

_CH_TYPES = {
    "str": "Nullable(String)",
    "int": "Nullable(Int64)",
    "float": "Nullable(Float64)",
    "bool": "Nullable(Bool)",
    "date": "Nullable(Date32)",
    "datetime": "Nullable(DateTime64(3))",
}

_IDENT = re.compile(r"[^a-zA-Z0-9_]")
_FULL_TABLE = re.compile(r"^select\s+\*\s+from\s+([a-zA-Z_][a-zA-Z0-9_.]*)\s*$", re.I)


def fingerprint(source: str, sql: str) -> str:
    normalized = re.sub(r"\s+", " ", sql.strip())
    return hashlib.sha256(f"{source}\n{normalized}".encode()).hexdigest()[:16]


def _literal(value, ch_type: str) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "NULL"
        return repr(value)
    if isinstance(value, dt.datetime):
        return f"'{value.strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}'"
    if isinstance(value, dt.date):
        return f"'{value.isoformat()}'"
    if "Float" in ch_type or "Int" in ch_type:
        try:
            return repr(float(value)) if "Float" in ch_type else str(int(value))
        except (TypeError, ValueError):
            return "NULL"
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"


@dataclass
class CachedResult:
    result: QueryResult
    cache_hit: bool
    fingerprint: str
    cached_at: float              # epoch seconds
    source: str
    sql: str
    total_rows: int               # rows in the cache table (result may be truncated)
    alias: str | None = None


@dataclass
class CacheStats:
    hits: int = 0
    misses: int = 0
    queries_today: int = 0
    _day: str = field(default_factory=lambda: dt.date.today().isoformat())

    def record(self, hit: bool):
        today = dt.date.today().isoformat()
        if today != self._day:
            self._day, self.queries_today = today, 0
        self.queries_today += 1
        if hit:
            self.hits += 1
        else:
            self.misses += 1


class CacheManager:
    def __init__(self, backend: CacheBackend | None = None, db: str | None = None,
                 ttl_seconds: int | None = None):
        self.backend = backend or make_backend()
        self.db = db or settings.cache_db
        self.ttl = ttl_seconds if ttl_seconds is not None else settings.cache_ttl_seconds
        self.stats = CacheStats()
        self._lock = threading.Lock()
        self._init_schema()

    def _init_schema(self):
        self.backend.command(f"CREATE DATABASE IF NOT EXISTS {self.db}")
        self.backend.command(f"""
            CREATE TABLE IF NOT EXISTS {self.db}.cache_entries (
                fingerprint String,
                source String,
                sql String,
                table_name String,
                alias String,
                cached_at DateTime64(3),
                row_count UInt64,
                ttl_seconds UInt32
            ) ENGINE = ReplacingMergeTree(cached_at) ORDER BY fingerprint
        """)

    # ------------------------------------------------------------------ #
    def execute(self, source_name: str, sql: str, *, force_refresh: bool = False,
                limit: int | None = None, datasource_manager=None) -> CachedResult:
        """The main entry point: serve from ClickHouse if fresh, otherwise
        pull from the origin datasource and cache the result."""
        from datasources.registry import get_manager
        manager = datasource_manager or get_manager()

        sql = ensure_select_only(sql)
        fp = fingerprint(source_name, sql)
        limit = limit or settings.result_row_limit

        entry = self._lookup(fp)
        if entry and not force_refresh and self._is_fresh(entry):
            result = self.backend.query(
                f"SELECT * FROM {self.db}.{entry['table_name']} LIMIT {int(limit)}"
            )
            self.stats.record(hit=True)
            return CachedResult(
                result=result, cache_hit=True, fingerprint=fp,
                cached_at=entry["cached_at"], source=source_name, sql=sql,
                total_rows=entry["row_count"], alias=entry["alias"] or None,
            )

        source = manager.get(source_name)
        origin = source.query(sql)
        alias = self._derive_alias(source_name, sql)
        self._store(fp, source_name, sql, origin, alias)
        self.stats.record(hit=False)

        shown = origin.rows[:limit]
        result = QueryResult(
            columns=origin.columns, types=origin.types, rows=shown,
            row_count=len(shown), elapsed_ms=origin.elapsed_ms,
            truncated=len(origin.rows) > len(shown),
        )
        return CachedResult(
            result=result, cache_hit=False, fingerprint=fp, cached_at=time.time(),
            source=source_name, sql=sql, total_rows=len(origin.rows), alias=alias,
        )

    def query_cached(self, sql: str, limit: int | None = None) -> QueryResult:
        """Run ad-hoc SQL directly against the ClickHouse cache database —
        this is what the Analysis pivot and follow-up questions use, so
        they never re-hit the origin warehouse."""
        sql = ensure_select_only(sql)
        limit = limit or settings.result_row_limit
        return self.backend.query(f"SELECT * FROM ({sql}) LIMIT {int(limit)}")

    def ensure_cached_table(self, source_name: str, table: str,
                            force_refresh: bool = False) -> CachedResult:
        """Guarantees `<source>__<table>` exists as a queryable view over
        cached data, pulling from the origin if needed."""
        return self.execute(source_name, f"SELECT * FROM {table}",
                            force_refresh=force_refresh)

    # ------------------------------------------------------------------ #
    def _derive_alias(self, source_name: str, sql: str) -> str | None:
        m = _FULL_TABLE.match(sql.strip())
        if not m:
            return None
        table = m.group(1).split(".")[-1]
        return _IDENT.sub("_", f"{source_name}__{table}")

    def _lookup(self, fp: str) -> dict | None:
        result = self.backend.query(
            f"SELECT table_name, alias, toUnixTimestamp64Milli(cached_at) AS ts, "
            f"row_count, ttl_seconds FROM {self.db}.cache_entries "
            f"WHERE fingerprint = '{fp}' ORDER BY cached_at DESC LIMIT 1"
        )
        if not result.rows:
            return None
        row = result.rows[0]
        return {
            "table_name": row[0], "alias": row[1],
            "cached_at": float(row[2]) / 1000.0,
            "row_count": int(row[3]), "ttl_seconds": int(row[4]),
        }

    def _is_fresh(self, entry: dict) -> bool:
        ttl = entry["ttl_seconds"] or self.ttl
        return (time.time() - entry["cached_at"]) < ttl

    def _store(self, fp: str, source_name: str, sql: str, origin: QueryResult,
               alias: str | None):
        table = f"r_{fp}"
        types = origin.types
        if not types or all(t == "str" for t in types):
            types = infer_types_from_rows(origin.columns, origin.rows)
        ch_types = [_CH_TYPES.get(t, "Nullable(String)") for t in types]
        safe_cols = [_IDENT.sub("_", c) or f"col_{i}" for i, c in enumerate(origin.columns)]
        cols_ddl = ", ".join(f"`{c}` {t}" for c, t in zip(safe_cols, ch_types))

        with self._lock:
            self.backend.command(f"DROP TABLE IF EXISTS {self.db}.{table}")
            self.backend.command(
                f"CREATE TABLE {self.db}.{table} ({cols_ddl}) "
                f"ENGINE = MergeTree ORDER BY tuple()"
            )
            for chunk_start in range(0, len(origin.rows), 1000):
                chunk = origin.rows[chunk_start:chunk_start + 1000]
                values = ",".join(
                    "(" + ",".join(_literal(v, t) for v, t in zip(row, ch_types)) + ")"
                    for row in chunk
                )
                if values:
                    self.backend.command(f"INSERT INTO {self.db}.{table} VALUES {values}")
            now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            self.backend.command(
                f"INSERT INTO {self.db}.cache_entries VALUES ("
                f"'{fp}', {_literal(source_name, 'String')}, {_literal(sql, 'String')}, "
                f"'{table}', {_literal(alias or '', 'String')}, '{now}', "
                f"{len(origin.rows)}, {int(self.ttl)})"
            )
            if alias:
                self.backend.command(
                    f"CREATE OR REPLACE VIEW {self.db}.`{alias}` "
                    f"AS SELECT * FROM {self.db}.{table}"
                )

    # ------------------------------------------------------------------ #
    def entries(self) -> list[dict]:
        result = self.backend.query(
            f"SELECT fingerprint, source, sql, table_name, alias, "
            f"toUnixTimestamp64Milli(cached_at) AS ts, row_count, ttl_seconds "
            f"FROM {self.db}.cache_entries FINAL ORDER BY cached_at DESC"
        )
        now = time.time()
        entries = []
        for r in result.rows:
            cached_at = float(r[5]) / 1000.0
            age = now - cached_at
            ttl = int(r[7]) or self.ttl
            entries.append({
                "fingerprint": r[0], "source": r[1], "sql": r[2],
                "table_name": r[3], "alias": r[4] or None,
                "cached_at": cached_at, "age_seconds": age,
                "row_count": int(r[6]), "ttl_seconds": ttl,
                "fresh": age < ttl,
            })
        return entries

    def source_rollup(self) -> dict[str, dict]:
        """Per-source cache freshness for the UI: newest entry age, table
        count, total cached rows."""
        rollup: dict[str, dict] = {}
        for e in self.entries():
            r = rollup.setdefault(e["source"], {
                "tables": 0, "rows": 0, "newest_age": None, "fresh": False,
            })
            r["tables"] += 1
            r["rows"] += e["row_count"]
            if r["newest_age"] is None or e["age_seconds"] < r["newest_age"]:
                r["newest_age"] = e["age_seconds"]
                r["fresh"] = e["fresh"]
        return rollup

    def invalidate(self, fingerprint_or_source: str) -> int:
        """Drops matching entries (by fingerprint or by source name) so the
        next query re-pulls from the origin. Returns entries removed."""
        removed = 0
        for e in self.entries():
            if fingerprint_or_source in (e["fingerprint"], e["source"]):
                self.backend.command(f"DROP TABLE IF EXISTS {self.db}.{e['table_name']}")
                if e["alias"]:
                    self.backend.command(f"DROP VIEW IF EXISTS {self.db}.`{e['alias']}`")
                self.backend.command(
                    f"ALTER TABLE {self.db}.cache_entries "
                    f"DELETE WHERE fingerprint = '{e['fingerprint']}'"
                )
                removed += 1
        return removed


_cache: CacheManager | None = None


def get_cache() -> CacheManager:
    global _cache
    if _cache is None:
        _cache = CacheManager()
    return _cache


def reset_cache(**kwargs) -> CacheManager:
    """Test hook: rebuild the singleton with explicit backend/db/ttl."""
    global _cache
    _cache = CacheManager(**kwargs)
    return _cache
