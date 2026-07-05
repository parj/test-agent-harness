"""
Configuration for the FinOps agent.
Set PROVIDER to "anthropic", "openai", or "gemini" to choose the chat LLM.
Embeddings always go through OpenAI regardless of chat provider, since
Anthropic and Gemini don't expose an embeddings endpoint.
"""
import os
from dataclasses import dataclass


@dataclass
class Settings:
    provider: str = os.environ.get("PROVIDER", "anthropic")  # anthropic | openai | gemini

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
        "DB_PATH",
        os.path.join(os.path.dirname(__file__), "..", "sample_data", "finops.db"),
    )
    postgres_dsn: str = os.environ.get(
        "POSTGRES_DSN", "postgresql://finops:localdev@localhost:5432/finops"
    )
    skills_dir: str = os.environ.get(
        "SKILLS_DIR", os.path.join(os.path.dirname(__file__), "..", "skills")
    )


settings = Settings()
