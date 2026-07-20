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


class Provider(ABC):
    @abstractmethod
    def complete(
        self,
        messages: list[dict],
        system: str,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """One completion. `tools` is the provider-agnostic schema list from
        tools.base.get_tool_schemas()."""
