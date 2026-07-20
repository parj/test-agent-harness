"""
Terminal chat loop for testing the agent without the web UI.

    cd src && python chat_cli.py

Uses whichever PROVIDER is configured (falls back to the offline stub if
the key is missing), with query_data routed through the ClickHouse cache.
"""
import asyncio

import tools  # noqa: F401 — registers tools
from agent.runtime import AgentRuntime


def _on_event(kind: str, data: dict):
    if kind == "tool_start":
        print(f"  [tool] {data['tool']}: {(data.get('input') or {}).get('sql', '')[:100]}")
    elif kind == "tool_result":
        print(f"  [tool] → {str(data.get('result'))[:120]}")


async def main():
    runtime = AgentRuntime()
    conversation: list[dict] = []
    print("FinAgent CLI — ask about the finance data (Ctrl-D to exit)")
    while True:
        try:
            user = input("\nyou> ").strip()
        except EOFError:
            print()
            break
        if not user:
            continue
        conversation.append({"role": "user", "content": user})
        text, conversation = await runtime.run(conversation, on_event=_on_event)
        print(f"\nagent> {text}")


if __name__ == "__main__":
    asyncio.run(main())
