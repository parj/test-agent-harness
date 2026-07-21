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
    openai_model: str = os.environ.get("OPENAI_MODEL", "gpt-5.4-nano")

    gemini_api_key: str = os.environ.get("GEMINI_API_KEY", "")
    gemini_model: str = os.environ.get("GEMINI_MODEL", "gemini-2.5-pro")

    # Embeddings (semantic memory) — always OpenAI
    embedding_api_key: str = os.environ.get("OPENAI_API_KEY", "")
    embedding_model: str = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
    memory_retrieval_count: int = int(os.environ.get("MEMORY_RETRIEVAL_COUNT", "5"))

    max_tool_iterations: int = int(os.environ.get("MAX_TOOL_ITERATIONS", "10"))
    context_token_limit: int = int(os.environ.get("CONTEXT_TOKEN_LIMIT", "100000"))

    # --- Context-window compaction ---
    # Model context windows, by provider. These drift as vendors ship new
    # models — check current docs before trusting these in production; they're
    # sane defaults for the models configured above, not hard guarantees.
    anthropic_context_window: int = int(os.environ.get("ANTHROPIC_CONTEXT_WINDOW", "200000"))
    openai_context_window: int = int(os.environ.get("OPENAI_CONTEXT_WINDOW", "200000"))
    gemini_context_window: int = int(os.environ.get("GEMINI_CONTEXT_WINDOW", "1000000"))
    stub_context_window: int = int(os.environ.get("STUB_CONTEXT_WINDOW", "50000"))
    # Warn the user once a conversation's last LLM call used this fraction of
    # the model's context window; auto-compact once it hits the compact ratio.
    context_warn_ratio: float = float(os.environ.get("CONTEXT_WARN_RATIO", "0.75"))
    context_compact_ratio: float = float(os.environ.get("CONTEXT_COMPACT_RATIO", "0.90"))

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
    # A task that finishes answering goes to "pending_user" (the agent may
    # still get a follow-up) rather than straight to "complete". If no
    # follow-up arrives within this window it's auto-marked complete.
    task_followup_idle_seconds: int = int(os.environ.get("TASK_FOLLOWUP_IDLE_SECONDS", "3600"))

    # --- Server ---
    server_host: str = os.environ.get("SERVER_HOST", "127.0.0.1")
    server_port: int = int(os.environ.get("SERVER_PORT", "8720"))
    demo_user: str = os.environ.get("DEMO_USER", "Sarah Chen")

    # --- Usage profiling (nightly "sleep" consolidation) ---
    # UTC hour the in-process scheduler runs the nightly consolidation job.
    consolidation_hour_utc: int = int(os.environ.get("CONSOLIDATION_HOUR_UTC", "2"))
    # Raw activity_log rows older than this are dropped once folded into the
    # user's profile — keeps a short debugging tail without growing forever.
    activity_retention_days: int = int(os.environ.get("ACTIVITY_RETENTION_DAYS", "7"))

    # --- OpenTelemetry ---
    # Exported to the SigNoz OTel collector (docker-compose brings it up on
    # 4318/http). Set OTEL_ENABLED=false to disable instrumentation entirely
    # (e.g. offline test runs) without touching code.
    otel_enabled: bool = os.environ.get("OTEL_ENABLED", "true").lower() == "true"
    otel_exporter_endpoint: str = os.environ.get(
        "OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318"
    )
    otel_service_name: str = os.environ.get("OTEL_SERVICE_NAME", "finagent-api")
    # Separate service name for browser RUM telemetry (clicks, page timing)
    # relayed through POST /api/rum — kept distinct so SigNoz's service list
    # and the Web Vitals dashboard treat "the website" as its own service.
    otel_rum_service_name: str = os.environ.get("OTEL_RUM_SERVICE_NAME", "finagent-web")
    # Base URL of the SigNoz UI (not the OTLP collector) — used to build
    # "open this trace" links in the task detail view.
    signoz_url: str = os.environ.get("SIGNOZ_URL", "http://localhost:8080")


settings = Settings()


def context_window() -> int:
    """Context window (tokens) for the currently configured chat provider."""
    return {
        "anthropic": settings.anthropic_context_window,
        "openai": settings.openai_context_window,
        "gemini": settings.gemini_context_window,
        "stub": settings.stub_context_window,
    }.get(settings.provider, settings.stub_context_window)
