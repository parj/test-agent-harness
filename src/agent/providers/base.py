"""
Provider interface. The runtime speaks only this shape; each adapter
translates it to its SDK's wire format.

Internal message format (provider-agnostic):
  {"role": "user", "content": str}
  {"role": "assistant", "content": str, "tool_calls": [{"id","name","input"}]}
  {"role": "tool_result", "tool_call_id": str, "name": str, "content": str}
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    # Raw provider-native reasoning/thinking blocks (Anthropic extended
    # thinking only, currently). Opaque to the runtime — stashed on the
    # assistant message so the same provider can echo them back verbatim
    # on the next turn, which the Anthropic API requires when a
    # thinking-enabled turn is followed by tool use.
    thinking_blocks: list[dict] = field(default_factory=list)


# Effort levels the New Task UI offers, in ascending order.
REASONING_EFFORTS = ("low", "medium", "high")


class Provider(ABC):
    @abstractmethod
    def complete(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict] | None = None,
        reasoning_effort: str | None = None,
    ) -> LLMResponse:
        """One completion. `tools` is the provider-agnostic schema list from
        tools.base.get_tool_schemas(). `reasoning_effort` is one of
        REASONING_EFFORTS or None (provider default); adapters map it to
        their own reasoning/thinking control and silently ignore it if the
        configured model doesn't support one."""
