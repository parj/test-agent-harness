"""Anthropic adapter — translates the internal format to the Messages API."""
from agent.providers.base import LLMResponse, Provider, ToolCall
from config import settings

# Extended-thinking token budgets per effort level. Anthropic has no named
# "effort" levels (unlike OpenAI) — thinking is a token budget, so these are
# our own mapping.
_THINKING_BUDGETS = {"low": 1024, "medium": 4096, "high": 16000}


class AnthropicProvider(Provider):
    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def complete(self, messages, system, tools=None, reasoning_effort=None) -> LLMResponse:
        api_messages = []
        for m in messages:
            if m["role"] == "user":
                api_messages.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                # Thinking blocks (if any) must lead the content list and be
                # echoed back verbatim — the API requires this when a
                # thinking-enabled turn was followed by tool use.
                blocks = list(m.get("thinking_blocks") or [])
                if m.get("content"):
                    blocks.append({"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls", []):
                    blocks.append({
                        "type": "tool_use", "id": tc["id"],
                        "name": tc["name"], "input": tc["input"],
                    })
                api_messages.append({"role": "assistant", "content": blocks})
            elif m["role"] == "tool_result":
                api_messages.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": m["tool_call_id"],
                        "content": m["content"],
                    }],
                })

        api_tools = [
            {"name": t["name"], "description": t["description"],
             "input_schema": t["input_schema"]}
            for t in (tools or [])
        ]

        max_tokens = 4096
        extra = {}
        budget = _THINKING_BUDGETS.get(reasoning_effort)
        if budget:
            max_tokens = budget + 4096
            extra["thinking"] = {"type": "enabled", "budget_tokens": budget}

        response = self.client.messages.create(
            model=settings.anthropic_model,
            max_tokens=max_tokens,
            system=system,
            messages=api_messages,
            tools=api_tools or None,
            **extra,
        )

        text_parts, tool_calls, thinking_blocks = [], [], []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))
            elif block.type == "thinking":
                thinking_blocks.append({
                    "type": "thinking", "thinking": block.thinking, "signature": block.signature,
                })
            elif block.type == "redacted_thinking":
                thinking_blocks.append({"type": "redacted_thinking", "data": block.data})

        return LLMResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            thinking_blocks=thinking_blocks,
        )
