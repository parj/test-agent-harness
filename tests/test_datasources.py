"""DuckDB connector (live) and registry behaviour. Postgres/ClickHouse/
Trino connectors share the same interface; their query paths are exercised
against live servers only when the corresponding DSNs are configured."""
import pytest

from datasources.base import QueryResult
from datasources.registry import KINDS


def test_all_four_kinds_registered():
    assert set(KINDS) == {"clickhouse", "postgres", "duckdb", "trino"}


def test_registry_loads_config(manager):
    assert set(manager.names()) == {"finops_erp", "bank_feed"}
    assert manager.default_source == "finops_erp"


def test_duckdb_query(manager):
    src = manager.get("finops_erp")
    result = src.query("SELECT COUNT(*) AS n FROM gl_entries")
    assert isinstance(result, QueryResult)
    assert result.columns == ["n"]
    assert result.rows[0][0] > 1000


def test_duckdb_limit_and_truncation(manager):
    src = manager.get("finops_erp")
    result = src.query("SELECT * FROM gl_entries", limit=10)
    assert result.row_count == 10
    assert result.truncated is True


def test_duckdb_list_tables(manager):
    names = {t.name for t in manager.get("finops_erp").list_tables()}
    assert {"gl_entries", "accounts", "budget", "vendors", "ap_invoices"} <= names


def test_duckdb_readonly(manager):
    src = manager.get("finops_erp")
    with pytest.raises(ValueError):
        src.query("DROP TABLE gl_entries")


def test_estimate_rows(manager):
    src = manager.get("finops_erp")
    est = src.estimate_rows("SELECT * FROM ap_invoices")
    assert est == 20_000


def test_second_source(manager):
    result = manager.get("bank_feed").query("SELECT COUNT(*) FROM bank_balances")
    assert result.rows[0][0] == 3


def test_unknown_source(manager):
    with pytest.raises(KeyError):
        manager.get("nope")
