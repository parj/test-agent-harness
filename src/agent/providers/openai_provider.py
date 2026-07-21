"""OpenAI adapter — translates the internal format to chat.completions."""
import json

from agent.providers.base import LLMResponse, Provider, ToolCall
from config import settings


class OpenAIProvider(Provider):
    def __init__(self):
        from openai import OpenAI
        self.client = OpenAI(api_key=settings.openai_api_key)

    def complete(self, messages, system, tools=None, reasoning_effort=None) -> LLMResponse:
        api_messages = [{"role": "system", "content": system}]
        for m in messages:
            if m["role"] == "user":
                api_messages.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                entry = {"role": "assistant", "content": m.get("content") or None}
                if m.get("tool_calls"):
                    entry["tool_calls"] = [
                        {"id": tc["id"], "type": "function",
                         "function": {"name": tc["name"],
                                      "arguments": json.dumps(tc["input"])}}
                        for tc in m["tool_calls"]
                    ]
                api_messages.append(entry)
            elif m["role"] == "tool_result":
                api_messages.append({
                    "role": "tool", "tool_call_id": m["tool_call_id"],
                    "content": m["content"],
                })

        api_tools = [
            {"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["input_schema"]}}
            for t in (tools or [])
        ]

        kwargs = dict(model=settings.openai_model, messages=api_messages, tools=api_tools or None)
        try:
            response = self.client.chat.completions.create(
                reasoning_effort=reasoning_effort, **kwargs,
            ) if reasoning_effort else self.client.chat.completions.create(**kwargs)
        except Exception:
            # Configured model doesn't support reasoning_effort (e.g. gpt-4o) —
            # retry without it rather than failing the whole task.
            response = self.client.chat.completions.create(**kwargs)

        choice = response.choices[0].message
        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name,
                     input=json.loads(tc.function.arguments or "{}"))
            for tc in (choice.tool_calls or [])
        ]
        usage = response.usage
        return LLMResponse(
            text=choice.content or "",
            tool_calls=tool_calls,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )
