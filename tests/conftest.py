"""
Shared fixtures. Everything runs against the offline stack: seeded DuckDB
sample data as the origin datasource, chdb (embedded ClickHouse) as the
cache backend, and the stub LLM provider — no network, no API keys.
"""
import os
import sys

os.environ.setdefault("PROVIDER", "stub")

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import pytest  # noqa: E402

import tools  # noqa: E402, F401 — registers query_data/list_sources
from db import seed  # noqa: E402


@pytest.fixture(scope="session")
def sample_data(tmp_path_factory):
    """Seeds a session-scoped copy of the sample data and points the
    datasource registry at it."""
    data_dir = tmp_path_factory.mktemp("sample_data")
    erp = str(data_dir / "finops.duckdb")
    bank = str(data_dir / "bank_feed.duckdb")
    seed.AP_INVOICE_COUNT = 20_000  # plenty for tests, seeds fast
    seed.seed_erp(erp)
    seed.seed_bank(bank)
    seed.SAMPLE_DIR = str(data_dir)
    seed.write_datasources_config(str(data_dir))
    return {"dir": str(data_dir), "erp": erp, "bank": bank,
            "config": str(data_dir / "datasources.json")}


@pytest.fixture(scope="session")
def manager(sample_data):
    from datasources import registry
    return registry.reset_manager(sample_data["config"])


@pytest.fixture(scope="session")
def cache(sample_data, tmp_path_factory):
    """Session-scoped chdb-backed cache (chdb allows one live session per
    process, so tests share it and use distinct queries/TTLs)."""
    from cache.backends import ChdbBackend
    from cache.manager import reset_cache
    chdb_dir = str(tmp_path_factory.mktemp("chdb"))
    return reset_cache(backend=ChdbBackend(chdb_dir), db="test_cache", ttl_seconds=300)
