"""
Trino connector via the official trino DBAPI client. Estimation uses
EXPLAIN (TYPE DISTRIBUTED) output row estimates where available.
"""
import re

from datasources.base import (
    Datasource, QueryResult, TableInfo, ensure_select_only, normalize_type,
)


class TrinoSource(Datasource):
    kind = "trino"

    def __init__(self, name: str, params: dict | None = None):
        super().__init__(name, params)

    def _connect(self):
        import trino
        auth = None
        if self.params.get("password"):
            auth = trino.auth.BasicAuthentication(
                self.params.get("user", "finagent"), self.params["password"]
            )
        return trino.dbapi.connect(
            host=self.params.get("host", "localhost"),
            port=int(self.params.get("port", 8080)),
            user=self.params.get("user", "finagent"),
            catalog=self.params.get("catalog", "hive"),
            schema=self.params.get("schema", "default"),
            http_scheme=self.params.get("http_scheme", "http"),
            auth=auth,
            request_timeout=30,
        )

    def query(self, sql: str, limit: int | None = None) -> QueryResult:
        sql = ensure_select_only(sql)
        elapsed = self._timed()
        con = self._connect()
        try:
            cur = con.cursor()
            cur.execute(sql)
            if limit is not None:
                rows = cur.fetchmany(limit + 1)
                truncated = len(rows) > limit
                rows = rows[:limit]
            else:
                rows = cur.fetchall()
                truncated = False
            columns = [d[0] for d in cur.description]
            types = [normalize_type(str(d[1])) for d in cur.description]
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
            cur = con.cursor()
            cur.execute("SHOW TABLES")
            return [TableInfo(name=r[0]) for r in cur.fetchall()]
        finally:
            con.close()

    def estimate_rows(self, sql: str) -> int | None:
        try:
            inner = ensure_select_only(sql)
            con = self._connect()
            try:
                cur = con.cursor()
                cur.execute(f"EXPLAIN {inner}")
                text = "\n".join(str(r[0]) for r in cur.fetchall())
                estimates = re.findall(r"rows:\s*([\d,]+)", text)
                if estimates:
                    return int(estimates[0].replace(",", ""))
            finally:
                con.close()
        except Exception:
            return None
        return None

    def display_params(self) -> dict:
        return {"connection": {
            "host": self.params.get("host", "localhost"),
            "port": self.params.get("port", 8080),
            "catalog": self.params.get("catalog", "hive"),
            "schema": self.params.get("schema", "default"),
        }}
