"""
DuckDB connector — embedded analytical database, used for local files
(the seeded sample data ships as one). Opens read-only per query so the
agent can never mutate the file and concurrent readers don't fight over
the write lock.
"""
import duckdb

from datasources.base import (
    Datasource, QueryResult, TableInfo, ensure_select_only, normalize_type,
)


class DuckDBSource(Datasource):
    kind = "duckdb"

    def __init__(self, name: str, params: dict | None = None):
        super().__init__(name, params)
        self.path = self.params.get("path", ":memory:")

    def _connect(self):
        if self.path == ":memory:":
            return duckdb.connect(":memory:")
        return duckdb.connect(self.path, read_only=True)

    def query(self, sql: str, limit: int | None = None) -> QueryResult:
        sql = ensure_select_only(sql)
        elapsed = self._timed()
        con = self._connect()
        try:
            cur = con.execute(sql)
            columns = [d[0] for d in cur.description]
            types = [normalize_type(str(d[1])) for d in cur.description]
            if limit is not None:
                rows = cur.fetchmany(limit + 1)
                truncated = len(rows) > limit
                rows = rows[:limit]
            else:
                rows = cur.fetchall()
                truncated = False
            rows = [list(r) for r in rows]
            return QueryResult(
                columns=columns, types=types, rows=rows,
                row_count=len(rows), elapsed_ms=elapsed(), truncated=truncated,
            )
        finally:
            con.close()

    def list_tables(self) -> list[TableInfo]:
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT table_name, estimated_size FROM duckdb_tables()"
            ).fetchall()
            return [TableInfo(name=r[0], row_count=r[1]) for r in rows]
        finally:
            con.close()

    def display_params(self) -> dict:
        return {"connection": {"path": self.path}}
