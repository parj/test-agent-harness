"""
PostgreSQL connector via psycopg 3 (sync; the server wraps calls in
asyncio.to_thread). Uses EXPLAIN's planner estimate for cost gating
instead of the COUNT(*) fallback so estimation never scans the table.
"""
import json

from datasources.base import (
    Datasource, QueryResult, TableInfo, ensure_select_only, normalize_type,
)


class PostgresSource(Datasource):
    kind = "postgres"

    def __init__(self, name: str, params: dict | None = None):
        super().__init__(name, params)
        self.dsn = self.params.get("dsn", "")

    def _connect(self):
        import psycopg
        return psycopg.connect(self.dsn, connect_timeout=5)

    def query(self, sql: str, limit: int | None = None) -> QueryResult:
        sql = ensure_select_only(sql)
        elapsed = self._timed()
        with self._connect() as con:
            con.read_only = True
            with con.cursor() as cur:
                cur.execute(sql)
                columns = [d.name for d in cur.description]
                type_names = []
                for d in cur.description:
                    try:
                        type_names.append(
                            normalize_type(con.adapters.types.get(d.type_code).name)
                        )
                    except Exception:
                        type_names.append("str")
                if limit is not None:
                    rows = cur.fetchmany(limit + 1)
                    truncated = len(rows) > limit
                    rows = rows[:limit]
                else:
                    rows = cur.fetchall()
                    truncated = False
                rows = [list(r) for r in rows]
        return QueryResult(
            columns=columns, types=type_names, rows=rows,
            row_count=len(rows), elapsed_ms=elapsed(), truncated=truncated,
        )

    def list_tables(self) -> list[TableInfo]:
        with self._connect() as con, con.cursor() as cur:
            cur.execute(
                """SELECT relname, reltuples::BIGINT FROM pg_class c
                   JOIN pg_namespace n ON n.oid = c.relnamespace
                   WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog','information_schema')
                   ORDER BY relname"""
            )
            return [TableInfo(name=r[0], row_count=max(r[1], 0)) for r in cur.fetchall()]

    def estimate_rows(self, sql: str) -> int | None:
        try:
            inner = ensure_select_only(sql)
            with self._connect() as con, con.cursor() as cur:
                cur.execute(f"EXPLAIN (FORMAT JSON) {inner}")
                plan = cur.fetchone()[0]
                if isinstance(plan, str):
                    plan = json.loads(plan)
                return int(plan[0]["Plan"]["Plan Rows"])
        except Exception:
            return None

    def display_params(self) -> dict:
        # show host/db only, never credentials
        try:
            import psycopg
            info = psycopg.conninfo.conninfo_to_dict(self.dsn)
            return {"connection": {
                "host": info.get("host", "?"), "port": info.get("port", "5432"),
                "dbname": info.get("dbname", "?"),
            }}
        except Exception:
            return {"connection": {}}
