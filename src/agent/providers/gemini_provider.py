"""Gemini adapter via google-genai. Known limitation (see plan.md): Gemini
doesn't return tool-call IDs, so the function name stands in as the ID —
fine for one call per tool per turn."""
from agent.providers.base import LLMResponse, Provider, ToolCall
from config import settings

# Thinking-token budgets per effort level, mirroring the Anthropic adapter —
# Gemini's thinking_budget is also a token count, not a named level.
_THINKING_BUDGETS = {"low": 1024, "medium": 4096, "high": 16000}


class GeminiProvider(Provider):
    def __init__(self):
        from google import genai
        self.client = genai.Client(api_key=settings.gemini_api_key)

    def complete(self, messages, system, tools=None, reasoning_effort=None) -> LLMResponse:
        from google.genai import types

        contents = []
        for m in messages:
            if m["role"] == "user":
                contents.append(types.Content(
                    role="user", parts=[types.Part(text=m["content"])]))
            elif m["role"] == "assistant":
                parts = []
                if m.get("content"):
                    parts.append(types.Part(text=m["content"]))
                for tc in m.get("tool_calls", []):
                    parts.append(types.Part(function_call=types.FunctionCall(
                        name=tc["name"], args=tc["input"])))
                if parts:
                    contents.append(types.Content(role="model", parts=parts))
            elif m["role"] == "tool_result":
                contents.append(types.Content(
                    role="user",
                    parts=[types.Part(function_response=types.FunctionResponse(
                        name=m["name"], response={"result": m["content"]}))],
                ))

        declarations = [
            types.FunctionDeclaration(
                name=t["name"], description=t["description"],
                parameters=_strip_schema(t["input_schema"]),
            )
            for t in (tools or [])
        ]

        config_kwargs = dict(
            system_instruction=system,
            tools=[types.Tool(function_declarations=declarations)] if declarations else None,
        )
        budget = _THINKING_BUDGETS.get(reasoning_effort)
        try:
            response = self.client.models.generate_content(
                model=settings.gemini_model, contents=contents,
                config=types.GenerateContentConfig(
                    **config_kwargs,
                    thinking_config=types.ThinkingConfig(thinking_budget=budget) if budget else None,
                ),
            )
        except Exception:
            # Configured model doesn't support thinking_config — retry without it
            # rather than failing the whole task.
            response = self.client.models.generate_content(
                model=settings.gemini_model, contents=contents,
                config=types.GenerateContentConfig(**config_kwargs),
            )

        text_parts, tool_calls = [], []
        candidate = response.candidates[0] if response.candidates else None
        for part in (candidate.content.parts if candidate and candidate.content else []):
            if part.text:
                text_parts.append(part.text)
            if part.function_call:
                tool_calls.append(ToolCall(
                    id=part.function_call.name,  # Gemini has no call IDs
                    name=part.function_call.name,
                    input=dict(part.function_call.args or {}),
                ))

        usage = response.usage_metadata
        return LLMResponse(
            text="\n".join(text_parts),
            tool_calls=tool_calls,
            input_tokens=(usage.prompt_token_count or 0) if usage else 0,
            output_tokens=(usage.candidates_token_count or 0) if usage else 0,
        )


def _strip_schema(schema: dict) -> dict:
    """Gemini rejects some JSONSchema keywords pydantic emits."""
    drop = {"title", "default", "additionalProperties", "$defs", "allOf", "anyOf"}
    if not isinstance(schema, dict):
        return schema
    return {
        k: ([_strip_schema(i) for i in v] if isinstance(v, list) else _strip_schema(v))
        for k, v in schema.items() if k not in drop
    }
