"""ClickHouse cache behaviour on the embedded (chdb) backend: miss → hit,
TTL expiry, forced refresh, invalidation, and ad-hoc SQL over cached data."""
import time


def test_miss_then_hit(manager, cache):
    sql = "SELECT account_code, SUM(amount) AS total FROM gl_entries GROUP BY account_code"
    first = cache.execute("finops_erp", sql, datasource_manager=manager)
    assert first.cache_hit is False
    assert first.total_rows == 12

    second = cache.execute("finops_erp", sql, datasource_manager=manager)
    assert second.cache_hit is True
    assert second.total_rows == 12
    assert sorted(map(tuple, second.result.rows)) == sorted(map(tuple, first.result.rows))


def test_whitespace_insensitive_fingerprint(manager, cache):
    a = cache.execute("finops_erp", "SELECT quarter, COUNT(*) AS n FROM gl_entries GROUP BY quarter",
                      datasource_manager=manager)
    b = cache.execute("finops_erp", "SELECT quarter,   COUNT(*) AS n\nFROM gl_entries GROUP BY quarter",
                      datasource_manager=manager)
    assert a.fingerprint == b.fingerprint
    assert b.cache_hit is True


def test_force_refresh(manager, cache):
    sql = "SELECT COUNT(*) AS n FROM vendors"
    cache.execute("finops_erp", sql, datasource_manager=manager)
    refreshed = cache.execute("finops_erp", sql, force_refresh=True,
                              datasource_manager=manager)
    assert refreshed.cache_hit is False


def test_ttl_expiry(manager, cache):
    sql = "SELECT COUNT(*) AS n FROM budget"
    old_ttl = cache.ttl
    cache.ttl = 1
    try:
        first = cache.execute("finops_erp", sql, datasource_manager=manager)
        assert first.cache_hit is False
        time.sleep(1.2)
        second = cache.execute("finops_erp", sql, datasource_manager=manager)
        assert second.cache_hit is False  # stale → re-pulled from origin
    finally:
        cache.ttl = old_ttl


def test_full_table_alias_and_adhoc_sql(manager, cache):
    cached = cache.execute("finops_erp", "SELECT * FROM accounts",
                           datasource_manager=manager)
    assert cached.alias == "finops_erp__accounts"
    # Follow-up analysis runs directly against ClickHouse, not the origin.
    result = cache.query_cached(
        f"SELECT COUNT(*) AS n FROM {cache.db}.finops_erp__accounts"
    )
    assert int(result.rows[0][0]) == 12


def test_entries_and_rollup(manager, cache):
    entries = cache.entries()
    assert entries, "expected cache entries from earlier tests"
    sources = {e["source"] for e in entries}
    assert "finops_erp" in sources
    rollup = cache.source_rollup()
    assert rollup["finops_erp"]["rows"] > 0
    assert rollup["finops_erp"]["fresh"] is True


def test_invalidate_by_source(manager, cache):
    sql = "SELECT COUNT(*) AS n FROM bank_transactions"
    cache.execute("bank_feed", sql, datasource_manager=manager)
    removed = cache.invalidate("bank_feed")
    assert removed >= 1
    after = cache.execute("bank_feed", sql, datasource_manager=manager)
    assert after.cache_hit is False


def test_string_escaping_roundtrip(manager, cache):
    # Value contains a quote and a literal backslash — must survive the
    # VALUES-literal insert into ClickHouse and read back identically.
    tricky = r"SELECT 'it''s a back\slash' AS s, memo FROM gl_entries LIMIT 2"
    cached = cache.execute("finops_erp", tricky, datasource_manager=manager)
    assert cached.result.rows[0][0] == "it's a back\\slash"
    again = cache.execute("finops_erp", tricky, datasource_manager=manager)
    assert again.cache_hit is True
    assert again.result.rows[0][0] == cached.result.rows[0][0]
