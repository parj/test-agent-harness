"""
The core agent loop. Provider-agnostic: works identically whether the
underlying model is Claude, GPT, or Gemini, because it only ever talks to
the Provider interface, never to a provider SDK directly.
"""
from agent.providers import get_provider
from config import settings
from tools.base import execute_tool, get_tool

SYSTEM_PROMPT = """You are a finance operations assistant. You have access to tools \
that query the company's financial data: chart of accounts, transactions, and budget.

When asked a question you can't answer directly, use the query_data tool to run a \
SELECT query against the finance database and answer from the real numbers. \
Always show your reasoning briefly, then give a clear, direct answer. \
Format currency as £X,XXX.XX. Never fabricate numbers — if you're unsure, query the data."""


class AgentRuntime:
    def __init__(self):
        self.provider = get_provider()

    async def run(
        self, conversation: list[dict], on_event=None, system_prompt: str | None = None
    ) -> tuple[str, list[dict]]:
        """
        Runs the reasoning loop for one user turn against whichever provider
        is configured (settings.provider). `conversation` and the return value
        both use the internal provider-agnostic message format — see
        agent/providers/base.py for the shape.

        `system_prompt`, if given, overrides the bare SYSTEM_PROMPT — this is
        how the context manager injects active skill instructions and
        relevant long-term memories for this turn.
        """
        messages = list(conversation)
        active_system_prompt = system_prompt or SYSTEM_PROMPT

        for iteration in range(settings.max_tool_iterations):
            response = self.provider.complete(messages, system=active_system_prompt)

            if on_event:
                on_event("llm_call", {
                    "iteration": iteration,
                    "provider": settings.provider,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                })

            messages.append({
                "role": "assistant",
                "content": response.text,
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "input": tc.input} for tc in response.tool_calls
                ],
            })

            if not response.tool_calls:
                return response.text or "", messages

            for call in response.tool_calls:
                reg = get_tool(call.name)

                if reg.requires_approval:
                    if on_event:
                        on_event("approval_needed", {"tool": call.name, "input": call.input})
                    messages.append({
                        "role": "tool_result",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": "This action requires human approval and has been paused. "
                                    "(Approval flow not wired up in this Phase 1 skeleton.)",
                    })
                    continue

                if on_event:
                    on_event("tool_start", {"tool": call.name, "input": call.input})

                try:
                    result = await execute_tool(call.name, call.input)
                    result_text = result.model_dump_json() if hasattr(result, "model_dump_json") else str(result)
                except Exception as e:
                    result_text = f"Tool error: {e}"

                if on_event:
                    on_event("tool_result", {"tool": call.name, "result": result_text})

                messages.append({
                    "role": "tool_result",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": result_text,
                })

        raise RuntimeError(f"Agent exceeded max_tool_iterations ({settings.max_tool_iterations})")
