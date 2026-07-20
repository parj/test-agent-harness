"""
Stub provider — a deterministic, offline "LLM" for the test agent
harness. No API key, no network. It exercises the exact same runtime
loop as the real providers: reads the conversation, decides on a
query_data tool call from keyword heuristics, then turns the tool result
into a written answer (including a real variance narrative when the
result shape supports it).
"""
import json
import uuid

from agent.providers.base import LLMResponse, Provider, ToolCall

_PATTERNS = [
    # (keywords-any, source, sql)
    (("software", "vendor"), None,
     "SELECT vendor, ROUND(SUM(amount), 0) AS total FROM gl_entries "
     "WHERE account_code = '6300' GROUP BY vendor ORDER BY total DESC"),
    (("variance", "q1", "q2", "ledger"), None,
     "SELECT account_name, quarter, ROUND(SUM(amount), 0) AS total "
     "FROM gl_entries GROUP BY account_name, quarter ORDER BY account_name, quarter"),
    (("aging", "ap ", "invoices", "payable"), None,
     "SELECT * FROM ap_invoices WHERE age_days > 30"),
    (("cash", "bank", "balance"), "bank_feed",
     "SELECT account_name, currency, balance, as_of FROM bank_balances ORDER BY balance DESC"),
    (("vendor payment", "recon",), None,
     "SELECT vendor, COUNT(*) AS payments, ROUND(SUM(amount), 0) AS total "
     "FROM gl_entries WHERE amount > 0 GROUP BY vendor ORDER BY total DESC"),
]

_DEFAULT_SQL = (
    "SELECT account_name, quarter, ROUND(SUM(amount), 0) AS total "
    "FROM gl_entries GROUP BY account_name, quarter ORDER BY account_name, quarter"
)


class StubProvider(Provider):
    def complete(self, messages, system, tools=None) -> LLMResponse:
        last = messages[-1] if messages else {}

        if last.get("role") == "tool_result":
            return LLMResponse(text=self._summarize(last), input_tokens=0, output_tokens=0)

        user_text = ""
        for m in reversed(messages):
            if m.get("role") == "user":
                user_text = (m.get("content") or "").lower()
                break

        source, sql = None, _DEFAULT_SQL
        for keywords, src, pattern_sql in _PATTERNS:
            if any(k in user_text for k in keywords):
                source, sql = src, pattern_sql
                break

        call_input = {"sql": sql}
        if source:
            call_input["source"] = source
        if "refresh" in user_text or "latest" in user_text:
            call_input["refresh"] = True

        return LLMResponse(
            text="Let me query the data.",
            tool_calls=[ToolCall(id=f"stub_{uuid.uuid4().hex[:8]}", name="query_data",
                                 input=call_input)],
        )

    # ------------------------------------------------------------------ #
    def _summarize(self, tool_msg: dict) -> str:
        content = tool_msg.get("content") or ""
        lowered = content.lower()
        if "denied" in lowered or "requires human approval" in lowered:
            return ("The query was not approved, so I stopped there. "
                    "Approve it from the task view or narrow the query and I'll retry.")
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return f"The tool returned: {content[:400]}"
        if "columns" not in payload:
            return f"Done. Result: {json.dumps(payload)[:400]}"

        columns = payload["columns"]
        rows = payload.get("rows", [])
        origin = ("the ClickHouse cache" if payload.get("cache_hit")
                  else f"{payload.get('source', 'the datasource')} (now cached in ClickHouse)")
        header = (f"I ran the query against {origin} — "
                  f"{payload.get('row_count', len(rows)):,} rows total.")

        narrative = self._variance_narrative(columns, rows)
        if narrative:
            return f"{header}\n\n{narrative}"

        if rows:
            preview = ", ".join(
                f"{columns[i]}={rows[0][i]}" for i in range(min(len(columns), 4))
            )
            return f"{header} First row: {preview}."
        return f"{header} The result set is empty."

    def _variance_narrative(self, columns, rows) -> str | None:
        """If the result looks like account/quarter/total, write a real
        Q-over-Q variance summary from the numbers."""
        try:
            ai = columns.index("account_name")
            qi = columns.index("quarter")
            ti = columns.index("total")
        except ValueError:
            return None
        by_account: dict[str, dict] = {}
        for r in rows:
            by_account.setdefault(str(r[ai]), {})[str(r[qi])] = float(r[ti] or 0)
        quarters = sorted({str(r[qi]) for r in rows})
        if len(quarters) < 2:
            return None
        q1, q2 = quarters[0], quarters[-1]
        movers = []
        for account, values in by_account.items():
            delta = values.get(q2, 0) - values.get(q1, 0)
            if abs(delta) > 0:
                movers.append((account, values.get(q1, 0), values.get(q2, 0), delta))
        movers.sort(key=lambda m: abs(m[3]), reverse=True)
        if not movers:
            return None
        lines = [f"Largest movers {q1} → {q2}:"]
        for account, v1, v2, delta in movers[:5]:
            direction = "up" if delta > 0 else "down"
            lines.append(
                f"- {account}: {v1:,.0f} → {v2:,.0f} ({direction} {abs(delta):,.0f})"
            )
        return "\n".join(lines)
