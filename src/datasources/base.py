"""
Datasource abstraction. Every connector (ClickHouse, Postgres, DuckDB,
Trino) exposes the same small surface: run a SELECT, list tables,
estimate how many rows a query would touch, and report health.

Connectors are synchronous — the async server runs them via
asyncio.to_thread so a slow warehouse never blocks the event loop.
"""
from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class QueryResult:
    columns: list[str]
    types: list[str]          # normalized: str|int|float|bool|date|datetime
    rows: list[list]
    row_count: int            # total rows produced (rows may be truncated)
    elapsed_ms: float = 0.0
    truncated: bool = False


@dataclass
class TableInfo:
    name: str
    row_count: int | None = None


@dataclass
class SourceStatus:
    connected: bool
    error: str | None = None


# Very small normalization of driver-reported types so cache table creation
# and the UI don't need to know every dialect's spelling.
_TYPE_PATTERNS = [
    (re.compile(r"bool", re.I), "bool"),
    (re.compile(r"int|long|serial", re.I), "int"),
    (re.compile(r"float|double|real|decimal|numeric", re.I), "float"),
    (re.compile(r"datetime|timestamp", re.I), "datetime"),
    (re.compile(r"date", re.I), "date"),
]


def normalize_type(raw: str | None) -> str:
    for pattern, norm in _TYPE_PATTERNS:
        if raw and pattern.search(raw):
            return norm
    return "str"


def infer_type(value) -> str:
    import datetime
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, datetime.datetime):
        return "datetime"
    if isinstance(value, datetime.date):
        return "date"
    return "str"


def infer_types_from_rows(columns: list[str], rows: list[list]) -> list[str]:
    types = []
    for i in range(len(columns)):
        t = "str"
        for row in rows[:100]:
            if row[i] is not None:
                t = infer_type(row[i])
                break
        types.append(t)
    return types


_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|attach|copy|merge|call|exec)\b",
    re.I,
)


def ensure_select_only(sql: str) -> str:
    """Guards the sandboxed query path: single statement, SELECT/WITH only,
    no DML/DDL keywords anywhere. Raises ValueError otherwise."""
    stripped = sql.strip().rstrip(";").strip()
    if ";" in stripped:
        raise ValueError("Only a single SQL statement is allowed")
    if not re.match(r"^(select|with|show|describe|desc)\b", stripped, re.I):
        raise ValueError("Only SELECT queries are allowed")
    if _FORBIDDEN.search(stripped):
        raise ValueError("Query contains a forbidden keyword (read-only access)")
    return stripped


class Datasource(ABC):
    kind: str = "base"

    def __init__(self, name: str, params: dict | None = None):
        self.name = name
        self.params = params or {}

    # -- required surface -------------------------------------------------
    @abstractmethod
    def query(self, sql: str, limit: int | None = None) -> QueryResult: ...

    @abstractmethod
    def list_tables(self) -> list[TableInfo]: ...

    def ping(self) -> SourceStatus:
        try:
            self.query("SELECT 1", limit=1)
            return SourceStatus(connected=True)
        except Exception as e:  # noqa: BLE001 — surface any driver error as status
            return SourceStatus(connected=False, error=str(e))

    def estimate_rows(self, sql: str) -> int | None:
        """Best-effort row estimate for cost gating. Wraps the query in a
        COUNT(*); connectors with a real planner override this."""
        try:
            inner = ensure_select_only(sql)
            result = self.query(f"SELECT COUNT(*) FROM ({inner}) AS _est", limit=1)
            if result.rows and result.rows[0]:
                return int(result.rows[0][0])
        except Exception:
            return None
        return None

    # -- shared helpers ---------------------------------------------------
    @staticmethod
    def _timed():
        start = time.perf_counter()
        return lambda: (time.perf_counter() - start) * 1000

    def describe(self) -> dict:
        return {"name": self.name, "kind": self.kind, **self.display_params()}

    def display_params(self) -> dict:
        """Connection info safe to show in the UI (no secrets)."""
        redacted = {}
        for k, v in self.params.items():
            if any(s in k.lower() for s in ("password", "secret", "token", "key")):
                continue
            redacted[k] = v
        return {"connection": redacted}
