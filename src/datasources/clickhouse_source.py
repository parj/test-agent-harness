"""
ClickHouse connector (as an origin datasource — distinct from the
ClickHouse *cache*, which lives in cache/). Talks to a server over the
HTTP interface via clickhouse-connect.
"""
from datasources.base import (
    Datasource, QueryResult, TableInfo, ensure_select_only, normalize_type,
)


class ClickHouseSource(Datasource):
    kind = "clickhouse"

    def __init__(self, name: str, params: dict | None = None):
        super().__init__(name, params)
        self._client = None

    def _connect(self):
        if self._client is None:
            import clickhouse_connect
            self._client = clickhouse_connect.get_client(
                host=self.params.get("host", "localhost"),
                port=int(self.params.get("port", 8123)),
                username=self.params.get("username", "default"),
                password=self.params.get("password", ""),
                database=self.params.get("database", "default"),
                connect_timeout=5,
            )
        return self._client

    def query(self, sql: str, limit: int | None = None) -> QueryResult:
        sql = ensure_select_only(sql)
        elapsed = self._timed()
        client = self._connect()
        result = client.query(sql)
        rows = [list(r) for r in result.result_rows]
        truncated = False
        if limit is not None and len(rows) > limit:
            rows, truncated = rows[:limit], True
        return QueryResult(
            columns=list(result.column_names),
            types=[normalize_type(str(t)) for t in result.column_types],
            rows=rows, row_count=len(rows), elapsed_ms=elapsed(), truncated=truncated,
        )

    def list_tables(self) -> list[TableInfo]:
        client = self._connect()
        db = self.params.get("database", "default")
        result = client.query(
            "SELECT name, total_rows FROM system.tables WHERE database = %(db)s",
            parameters={"db": db},
        )
        return [TableInfo(name=r[0], row_count=r[1]) for r in result.result_rows]

    def estimate_rows(self, sql: str) -> int | None:
        try:
            inner = ensure_select_only(sql)
            client = self._connect()
            result = client.query(f"EXPLAIN ESTIMATE {inner}")
            # columns: database, table, parts, rows, marks
            if result.result_rows:
                idx = list(result.column_names).index("rows")
                return int(sum(r[idx] for r in result.result_rows))
        except Exception:
            return super().estimate_rows(sql)
        return None

    def display_params(self) -> dict:
        return {"connection": {
            "host": self.params.get("host", "localhost"),
            "port": self.params.get("port", 8123),
            "database": self.params.get("database", "default"),
        }}
