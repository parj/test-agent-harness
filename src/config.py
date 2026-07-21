"""
Configuration for the FinOps agent.
Set PROVIDER to "anthropic", "openai", "gemini", or "stub" to choose the
chat LLM. "stub" is a deterministic offline provider used by the test
harness — it exercises the full tool loop without any API key.
Embeddings always go through OpenAI regardless of chat provider, since
Anthropic and Gemini don't expose an embeddings endpoint.
"""
import os
from dataclasses import dataclass

_HERE = os.path.dirname(__file__)


@dataclass
class Settings:
    provider: str = os.environ.get("PROVIDER", "anthropic")  # anthropic | openai | gemini | stub

    anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")
    anthropic_model: str = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    openai_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    openai_model: str = os.environ.get("OPENAI_MODEL", "gpt-4o")

    gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")
    gemini_model: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")

    # Embeddings (semantic memory) — always OpenAI
    embedding_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    embedding_model: str = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
    memory_retrieval_count: int = int(os.environ.get("MEMORY_RETRIEVAL_COUNT", "5"))

    max_tool_iterations: int = int(os.environ.get("MAX_TOOL_ITERATIONS", "10"))
    context_token_limit: int = int(os.environ.get("CONTEXT_TOKEN_LIMIT", "100000"))

    db_path: str = os.environ.get(
        "DB_PATH", os.path.join(_HERE, "..", "sample_data", "finops.duckdb")
    )
    postgres_dsn: str = os.environ.get(
        "POSTGRES_DSN", "postgresql://finops:localdev@localhost:5432/finops"
    )
    skills_dir: str = os.environ.get("SKILLS_DIR", os.path.join(_HERE, "..", "skills"))

    # --- Datasources ---
    # JSON file describing connected sources; see datasources/registry.py.
    datasources_config: str = os.environ.get(
        "DATASOURCES_CONFIG", os.path.join(_HERE, "..", "sample_data", "datasources.json")
    )

    # --- ClickHouse cache ---
    # When CLICKHOUSE_URL is set (e.g. http://localhost:8123) the cache uses
    # a real ClickHouse server via clickhouse-connect. Otherwise it falls
    # back to chdb, the embedded ClickHouse engine, persisted under chdb_dir.
    clickhouse_url: str = os.environ.get("CLICKHOUSE_URL", "")
    chdb_dir: str = os.environ.get(
        "CHDB_DIR", os.path.join(_HERE, "..", "sample_data", "chdb-cache")
    )
    cache_db: str = os.environ.get("CACHE_DB", "finagent_cache")
    cache_ttl_seconds: int = int(os.environ.get("CACHE_TTL_SECONDS", "900"))

    # --- Query governance ---
    # Queries whose estimated row count exceeds this need human approval
    # (when the task was created with approval required).
    approval_row_threshold: int = int(os.environ.get("APPROVAL_ROW_THRESHOLD", "100000"))
    # Rows returned to the model / UI per query are capped at this.
    result_row_limit: int = int(os.environ.get("RESULT_ROW_LIMIT", "500"))
    approval_timeout_seconds: int = int(os.environ.get("APPROVAL_TIMEOUT_SECONDS", "3600"))

    # --- Server ---
    server_host: str = os.environ.get("SERVER_HOST", "127.0.0.1")
    server_port: int = int(os.environ.get("SERVER_PORT", "8720"))
    demo_user: str = os.environ.get("DEMO_USER", "Sarah Chen")


settings = Settings()
