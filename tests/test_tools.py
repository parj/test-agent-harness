"""Tool registry, schema generation, and the SELECT-only guard."""
import pytest

from datasources.base import ensure_select_only
from tools.base import get_tool, get_tool_schemas


def test_tools_registered():
    assert get_tool("query_data").name == "query_data"
    assert get_tool("list_sources").name == "list_sources"


def test_schema_generation():
    schemas = {s["name"]: s for s in get_tool_schemas()}
    q = schemas["query_data"]
    assert q["description"]
    assert "sql" in q["input_schema"]["properties"]
    assert "source" in q["input_schema"]["properties"]
    assert "refresh" in q["input_schema"]["properties"]


def test_unknown_tool():
    with pytest.raises(KeyError):
        get_tool("rm_rf_slash")


@pytest.mark.parametrize("sql", [
    "SELECT * FROM t",
    "  select a, b from t where x = 1  ",
    "WITH x AS (SELECT 1) SELECT * FROM x",
])
def test_select_allowed(sql):
    assert ensure_select_only(sql)


@pytest.mark.parametrize("sql", [
    "DROP TABLE gl_entries",
    "SELECT 1; DROP TABLE gl_entries",
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET a = 1",
    "DELETE FROM t",
    "SELECT * FROM t; --",
    "CREATE TABLE evil (a INT)",
])
def test_mutations_blocked(sql):
    with pytest.raises(ValueError):
        ensure_select_only(sql)
