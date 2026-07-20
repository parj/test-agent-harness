"""
Datasource registry — loads connector definitions from a JSON config
file (settings.datasources_config) and hands out live connector
instances. Supported kinds: clickhouse, postgres, duckdb, trino.

Config shape:
{
  "default": "finops_erp",
  "sources": [
    {"name": "finops_erp", "kind": "duckdb", "icon": "🦆",
     "params": {"path": "sample_data/finops.duckdb"}},
    {"name": "app_db", "kind": "postgres", "icon": "🐘",
     "params": {"dsn": "postgresql://..."}},
    {"name": "warehouse", "kind": "clickhouse",
     "params": {"host": "...", "port": 8123, "database": "prod"}},
    {"name": "lakehouse", "kind": "trino",
     "params": {"host": "...", "catalog": "hive", "schema": "finance"}}
  ]
}
"""
import json
import os
import threading

from config import settings
from datasources.base import Datasource
from datasources.clickhouse_source import ClickHouseSource
from datasources.duckdb_source import DuckDBSource
from datasources.postgres_source import PostgresSource
from datasources.trino_source import TrinoSource

KINDS: dict[str, type[Datasource]] = {
    "clickhouse": ClickHouseSource,
    "postgres": PostgresSource,
    "duckdb": DuckDBSource,
    "trino": TrinoSource,
}

DEFAULT_ICONS = {"clickhouse": "⚡", "postgres": "🐘", "duckdb": "🦆", "trino": "🚄"}


class DatasourceManager:
    def __init__(self, config_path: str | None = None):
        self.config_path = config_path or settings.datasources_config
        self._lock = threading.Lock()
        self._sources: dict[str, Datasource] = {}
        self._meta: dict[str, dict] = {}       # name -> {icon, kind, ...}
        self.default_source: str | None = None
        self._load()

    def _load(self):
        if not os.path.exists(self.config_path):
            return
        with open(self.config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        for entry in config.get("sources", []):
            self._register(entry)
        self.default_source = config.get("default") or (
            next(iter(self._sources), None)
        )

    def _register(self, entry: dict) -> Datasource:
        kind = entry["kind"]
        if kind not in KINDS:
            raise ValueError(f"Unknown datasource kind: {kind} (supported: {list(KINDS)})")
        params = dict(entry.get("params", {}))
        # resolve relative duckdb paths against the config file's directory
        if kind == "duckdb" and "path" in params and not os.path.isabs(params["path"]):
            params["path"] = os.path.normpath(
                os.path.join(os.path.dirname(self.config_path), params["path"])
            )
        source = KINDS[kind](entry["name"], params)
        self._sources[source.name] = source
        self._meta[source.name] = {
            "icon": entry.get("icon", DEFAULT_ICONS.get(kind, "◉")),
            "kind": kind,
        }
        return source

    def add_source(self, entry: dict, persist: bool = True) -> Datasource:
        with self._lock:
            source = self._register(entry)
            if self.default_source is None:
                self.default_source = source.name
            if persist:
                self._persist()
            return source

    def _persist(self):
        config = {
            "default": self.default_source,
            "sources": [
                {
                    "name": name,
                    "kind": self._meta[name]["kind"],
                    "icon": self._meta[name]["icon"],
                    "params": self._sources[name].params,
                }
                for name in self._sources
            ],
        }
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)

    def get(self, name: str | None = None) -> Datasource:
        name = name or self.default_source
        if not name or name not in self._sources:
            raise KeyError(
                f"Unknown datasource: {name!r}. Connected sources: {list(self._sources)}"
            )
        return self._sources[name]

    def names(self) -> list[str]:
        return list(self._sources)

    def meta(self, name: str) -> dict:
        return self._meta.get(name, {})

    def describe_all(self) -> list[dict]:
        return [
            {**s.describe(), "icon": self._meta[name]["icon"],
             "is_default": name == self.default_source}
            for name, s in self._sources.items()
        ]


_manager: DatasourceManager | None = None


def get_manager() -> DatasourceManager:
    global _manager
    if _manager is None:
        _manager = DatasourceManager()
    return _manager


def reset_manager(config_path: str | None = None) -> DatasourceManager:
    """Test hook: point the singleton at a different config file."""
    global _manager
    _manager = DatasourceManager(config_path)
    return _manager
