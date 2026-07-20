"""Anthropic adapter — translates the internal format to the Messages API."""
from agent.providers.base import LLMResponse, Provider, ToolCall
from config import settings


class AnthropicProvider(Provider):
    def __init__(self):
        import anthropic
        self.client = anthropic.Anthropic(api_key=settings.anthropic_api_key)

    def complete(self, messages, system, tools=None) -> LLMResponse:
        api_messages = []
        for m in messages:
            if m["role"] == "user":
                api_messages.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                blocks = []
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

        response = self.client.messages.create(
            model=settings.anthropic_model,
            max_tokens=4096,
            system=system,
            messages=api_messages,
            tools=api_tools or None,
        )

        text_parts, tool_calls = [], []
        for block in response.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=block.input))

        return LLMResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
