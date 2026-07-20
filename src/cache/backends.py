"""
Cache storage backends. Both speak ClickHouse SQL — the only difference
is where the engine lives:

- ClickHouseServerBackend: a real ClickHouse server over HTTP
  (clickhouse-connect), selected when settings.clickhouse_url is set.
- ChdbBackend: chdb, the embedded in-process ClickHouse engine, persisted
  to a local directory. This is what the test harness uses — identical
  SQL semantics, no server required.
"""
import json
import threading
from abc import ABC, abstractmethod
from urllib.parse import urlparse

from config import settings
from datasources.base import QueryResult


class CacheBackend(ABC):
    label = "clickhouse"

    @abstractmethod
    def command(self, sql: str) -> None: ...

    @abstractmethod
    def query(self, sql: str) -> QueryResult: ...


class ChdbBackend(CacheBackend):
    label = "chdb (embedded ClickHouse)"

    def __init__(self, path: str | None = None):
        from chdb import session
        self._lock = threading.Lock()
        self._session = session.Session(path or settings.chdb_dir)

    def command(self, sql: str) -> None:
        with self._lock:
            self._session.query(sql)

    def query(self, sql: str) -> QueryResult:
        with self._lock:
            raw = str(self._session.query(sql, "JSONCompact"))
        payload = json.loads(raw) if raw.strip() else {"meta": [], "data": []}
        columns = [m["name"] for m in payload.get("meta", [])]
        types = [m["type"] for m in payload.get("meta", [])]
        rows = payload.get("data", [])
        return QueryResult(
            columns=columns, types=types, rows=rows, row_count=len(rows),
            elapsed_ms=float(payload.get("statistics", {}).get("elapsed", 0)) * 1000,
        )

    def close(self):
        with self._lock:
            self._session.close()


class ClickHouseServerBackend(CacheBackend):
    label = "clickhouse server"

    def __init__(self, url: str | None = None):
        import clickhouse_connect
        parsed = urlparse(url or settings.clickhouse_url)
        self._client = clickhouse_connect.get_client(
            host=parsed.hostname or "localhost",
            port=parsed.port or (8443 if parsed.scheme == "https" else 8123),
            username=parsed.username or "default",
            password=parsed.password or "",
            secure=parsed.scheme == "https",
        )

    def command(self, sql: str) -> None:
        self._client.command(sql)

    def query(self, sql: str) -> QueryResult:
        result = self._client.query(sql)
        return QueryResult(
            columns=list(result.column_names),
            types=[str(t) for t in result.column_types],
            rows=[list(r) for r in result.result_rows],
            row_count=len(result.result_rows),
        )

    def close(self):
        self._client.close()


def make_backend() -> CacheBackend:
    if settings.clickhouse_url:
        return ClickHouseServerBackend()
    return ChdbBackend()
