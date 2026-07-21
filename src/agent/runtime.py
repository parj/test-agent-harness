"""
The core agent loop. Provider-agnostic: works identically whether the
underlying model is Claude, GPT, Gemini, or the offline stub, because it
only ever talks to the Provider interface, never to a provider SDK
directly.

Approval flow: before a tool runs, its registration's approval check can
return an ApprovalRequest (e.g. query_data gating expensive scans). If
the caller supplied an `approval_handler`, the loop pauses on it — the
server turns that into a human Approve / Deny / Modify step in the UI —
and resumes with the decision. Without a handler the tool is skipped and
the model is told why (the old Phase-1 behaviour).
"""
import asyncio
from dataclasses import dataclass

from agent.providers import get_provider
from config import settings
from tools.base import execute_tool, get_tool, get_tool_schemas

SYSTEM_PROMPT = """You are a finance operations assistant. You have access to tools \
that query the company's financial data: chart of accounts, transactions, and budget.

When asked a question you can't answer directly, use the query_data tool to run a \
SELECT query against the finance database and answer from the real numbers. \
Results are cached in ClickHouse — repeated queries are served from the cache, and \
you can pass refresh=true when the user explicitly wants live data. \
Always show your reasoning briefly, then give a clear, direct answer. \
Format currency as £X,XXX.XX. Never fabricate numbers — if you're unsure, query the data."""


@dataclass
class ApprovalDecision:
    approved: bool
    modified_input: dict | None = None
    note: str = ""


class AgentRuntime:
    def __init__(self, provider=None):
        self.provider = provider or get_provider()

    async def run(
        self,
        conversation: list[dict],
        on_event=None,
        system_prompt: str | None = None,
        approval_handler=None,
        approvals_enabled: bool = True,
        reasoning_effort: str | None = None,
    ) -> tuple[str, list[dict]]:
        """
        Runs the reasoning loop for one user turn. `conversation` and the
        return value both use the internal provider-agnostic message format —
        see agent/providers/base.py for the shape.

        `system_prompt`, if given, overrides the bare SYSTEM_PROMPT — this is
        how the context manager injects active skill instructions and
        relevant long-term memories for this turn.

        `approval_handler`, if given, is an async callable
        (ApprovalRequest, ToolCall) -> ApprovalDecision awaited whenever a
        tool's approval check fires.

        `reasoning_effort`, if given, is passed to the provider on every
        call in this loop (see providers/base.py:REASONING_EFFORTS).
        """
        messages = list(conversation)
        active_system_prompt = system_prompt or SYSTEM_PROMPT
        tool_schemas = get_tool_schemas()

        for iteration in range(settings.max_tool_iterations):
            response = await asyncio.to_thread(
                self.provider.complete, messages,
                system=active_system_prompt, tools=tool_schemas,
                reasoning_effort=reasoning_effort,
            )

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
                    {"id": tc.id, "name": tc.name, "input": tc.input}
                    for tc in response.tool_calls
                ],
                "thinking_blocks": response.thinking_blocks,
            })

            if not response.tool_calls:
                return response.text or "", messages

            for call in response.tool_calls:
                reg = get_tool(call.name)

                request = None
                if approvals_enabled:
                    try:
                        request = await asyncio.to_thread(reg.check_approval, call.input)
                    except Exception:
                        request = None  # malformed input fails inside the tool instead

                if request is not None:
                    if on_event:
                        on_event("approval_needed", {
                            "tool": call.name, "input": call.input,
                            "summary": request.summary,
                            "estimated_rows": request.estimated_rows,
                            "estimated_cost": request.estimated_cost,
                            "detail": request.detail,
                        })
                    if approval_handler is None:
                        messages.append({
                            "role": "tool_result",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": "This action requires human approval and has "
                                       "been paused. (No approval handler wired in.)",
                        })
                        continue

                    decision = await approval_handler(request, call)
                    if not decision.approved:
                        if on_event:
                            on_event("approval_denied", {"tool": call.name,
                                                         "note": decision.note})
                        messages.append({
                            "role": "tool_result",
                            "tool_call_id": call.id,
                            "name": call.name,
                            "content": f"Denied by user: {decision.note or 'no reason given'}",
                        })
                        continue
                    if decision.modified_input is not None:
                        call.input = decision.modified_input
                    if on_event:
                        on_event("approval_granted", {"tool": call.name,
                                                      "input": call.input})

                if on_event:
                    on_event("tool_start", {"tool": call.name, "input": call.input})

                try:
                    result = await execute_tool(call.name, call.input)
                    result_text = (
                        result.model_dump_json()
                        if hasattr(result, "model_dump_json") else _to_json(result)
                    )
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


def _to_json(value) -> str:
    import json
    try:
        return json.dumps(value, default=str)
    except (TypeError, ValueError):
        return str(value)
