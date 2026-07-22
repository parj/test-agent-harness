"""Provider factory. Falls back to the offline stub provider when the
configured provider has no API key — the test harness must always run."""
import logging
import sys

from config import settings

logger = logging.getLogger(__name__)


def get_provider():
    provider = settings.provider

    key_by_provider = {
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
        "gemini": settings.gemini_api_key,
    }

    if provider in key_by_provider:
        has_key = bool(key_by_provider[provider])
        msg = f"[finagent] PROVIDER={provider} API key present: {has_key}"
        print(msg, file=sys.stderr)
        logger.info(msg)
        if not has_key:
            print(
                f"[finagent] PROVIDER={provider} but no API key set — "
                f"falling back to the offline stub provider.",
                file=sys.stderr,
            )
            provider = "stub"
    else:
        msg = f"[finagent] PROVIDER={provider} (no API key required)"
        print(msg, file=sys.stderr)
        logger.info(msg)

    msg = f"[finagent] Initializing provider: {provider}"
    print(msg, file=sys.stderr)
    logger.info(msg)

    if provider == "anthropic":
        from agent.providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider()
    if provider == "openai":
        from agent.providers.openai_provider import OpenAIProvider
        return OpenAIProvider()
    if provider == "gemini":
        from agent.providers.gemini_provider import GeminiProvider
        return GeminiProvider()
    if provider == "stub":
        from agent.providers.stub_provider import StubProvider
        return StubProvider()
    raise ValueError(f"Unknown PROVIDER: {settings.provider}")
