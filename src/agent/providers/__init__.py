"""Provider factory. Falls back to the offline stub provider when the
configured provider has no API key — the test harness must always run."""
import sys

from config import settings


def get_provider():
    provider = settings.provider

    key_by_provider = {
        "anthropic": settings.anthropic_api_key,
        "openai": settings.openai_api_key,
        "gemini": settings.gemini_api_key,
    }
    if provider in key_by_provider and not key_by_provider[provider]:
        print(
            f"[finagent] PROVIDER={provider} but no API key set — "
            f"falling back to the offline stub provider.",
            file=sys.stderr,
        )
        provider = "stub"

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
