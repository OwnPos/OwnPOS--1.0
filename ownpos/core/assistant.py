"""AI assistant: answers plain-language questions about the store's numbers.

Optional. Needs internet and an Anthropic API key (Settings > AI assistant); questions and the report rows it
looks at are sent to Anthropic to be answered.

What it can do is deliberately narrow:
- it can only RUN THE BUILT-IN REPORTS the signed-in employee is allowed to see, and look items up by name or
  code. There is no free SQL, so it can never read password or PIN hashes, licence data or the API key;
- it uses its own read-only database connection (PostgreSQL refuses any write on it) with a time limit,
  so it can never change a receipt or the books, and a slow question cannot hold up the registers."""
from __future__ import annotations

import json
from datetime import date

from .. import APP_NAME
from .db import Database, today_str
from .money import to_dollars
from .reports import REGISTRY, run_report

MAX_ROWS = 200

SYSTEM = """You are the bookkeeping and sales assistant inside {app}, the point-of-sale and accounting system
of {company}, a liquor store in Indiana, USA.

Facts about the data:
- Money is US dollars. The run_report tool returns money already in dollars.
- Indiana sales tax is 7%. Returns and voids are credit notes and are already netted in the reports.
- Today is {today} ({weekday}) by the store server's clock. The store's time zone is {tz}.
- Report keys, titles and parameters come from list_reports. Dates are YYYY-MM-DD.

How to work:
- Always get numbers from a tool; never guess or estimate. Say which report and which dates you used.
- Answer briefly and clearly, amounts like $1,234.56.
- You can only read reports. If asked to change something, explain where in OwnPOS to do it
  (for example Inventory > Item List, Receive & Adjust, End of Day, Settings).
- You are not an accountant or lawyer: for tax filing decisions, point to the Sales Tax reports and the accountant."""


def _tools():
    return [
        {"name": "list_reports", "description": "List the reports you may run, with keys and parameters.",
         "input_schema": {"type": "object", "properties": {}}},
        {"name": "run_report",
         "description": "Run a report. Money values in the result are dollars.",
         "input_schema": {"type": "object", "properties": {
             "key": {"type": "string", "description": "Report key from list_reports"},
             "date_from": {"type": "string", "description": "YYYY-MM-DD"},
             "date_to": {"type": "string", "description": "YYYY-MM-DD"},
             "department_id": {"type": "integer"}, "item_id": {"type": "integer"},
             "user_id": {"type": "integer"}, "account": {"type": "string"}, "days": {"type": "integer"}},
             "required": ["key"]}},
        {"name": "find_items",
         "description": "Find items by part of the name, item # or UPC. Returns id, item #, name, size, "
                        "department, price, on hand.",
         "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}},
        {"name": "list_departments", "description": "Department ids and names.",
         "input_schema": {"type": "object", "properties": {}}},
    ]


class Assistant:
    def __init__(self, conninfo: dict, settings: dict, user=None):
        self.ro = Database(conninfo, read_only=True)
        try:
            self.ro.conn.execute("SET statement_timeout = 20000")      # 20 s per query
        except Exception:  # noqa: BLE001
            pass
        self.settings = settings
        self.user = user
        self.history: list[dict] = []

    def available(self) -> tuple[bool, str]:
        if not self.settings.get("ai_enabled"):
            return False, "The AI assistant is switched off. The owner can turn it on in Settings > AI assistant."
        if not self.settings.get("ai_api_key"):
            return False, "Add an Anthropic API key in Settings > AI assistant."
        try:
            import anthropic  # noqa: F401
        except ImportError:
            return False, "The 'anthropic' package is not installed with this copy of OwnPOS."
        return True, ""

    def _allowed(self, rd) -> bool:
        return self.user is None or self.user.can(rd.perm)

    # ---- tools -------------------------------------------------------
    def _run_tool(self, name: str, args: dict) -> str:
        try:
            if name == "list_reports":
                return json.dumps([{"key": r.key, "title": r.title, "category": r.category, "params": r.params}
                                   for r in REGISTRY.values() if self._allowed(r)])
            if name == "run_report":
                key = args.pop("key", "")
                rd = REGISTRY.get(key)
                if rd is None:
                    return "Unknown report key. Use list_reports."
                if not self._allowed(rd):
                    return "The signed-in employee may not see that report."
                res = run_report(self.ro, key, args)
                rows = []
                for r in res.rows[:MAX_ROWS]:
                    out = {}
                    for k, label, typ in res.columns:
                        v = r.get(k)
                        out[label] = float(to_dollars(v)) if typ == "money" and v is not None else v
                    rows.append(out)
                return json.dumps({"title": res.title, "rows": rows, "row_count": len(res.rows),
                                   "truncated": len(res.rows) > MAX_ROWS, "notes": res.notes},
                                  ensure_ascii=False, default=str)
            if name == "find_items":
                text = f"%{str(args.get('text', '')).strip()}%"
                rows = self.ro.dicts(
                    "SELECT i.id, i.item_no, i.name, i.size, d.name AS department, i.price1, i.on_hand"
                    " FROM items i LEFT JOIN departments d ON d.id = i.department_id"
                    " WHERE i.name ILIKE ? OR i.item_no ILIKE ? OR i.upc ILIKE ? ORDER BY i.name LIMIT 50",
                    (text, text, text))
                for r in rows:
                    r["price"] = float(to_dollars(r.pop("price1")))
                return json.dumps(rows, default=str)
            if name == "list_departments":
                return json.dumps(self.ro.dicts("SELECT id, name FROM departments ORDER BY sort, name"))
        except Exception as exc:  # noqa: BLE001
            return f"Tool error: {exc}"
        return "Unknown tool"

    # ---- chat --------------------------------------------------------
    def ask(self, question: str) -> str:
        ok, why = self.available()
        if not ok:
            return why
        import anthropic
        client = anthropic.Anthropic(api_key=self.settings["ai_api_key"])
        today = date.fromisoformat(today_str())
        system = SYSTEM.format(app=APP_NAME, company=self.settings.get("company_name") or "the store",
                               today=today.isoformat(), weekday=f"{today:%A}",
                               tz=self.settings.get("timezone") or "America/Chicago")
        self.history.append({"role": "user", "content": question})
        messages = list(self.history)
        for _ in range(10):
            resp = client.messages.create(model=self.settings.get("ai_model") or "claude-haiku-5-5",
                                          max_tokens=2000, system=system, tools=_tools(), messages=messages)
            if resp.stop_reason != "tool_use":
                text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
                self.history.append({"role": "assistant", "content": text})
                self.history = self.history[-20:]
                return text or "(no answer)"
            messages.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if getattr(block, "type", "") == "tool_use":
                    results.append({"type": "tool_result", "tool_use_id": block.id,
                                    "content": self._run_tool(block.name, dict(block.input or {}))})
            messages.append({"role": "user", "content": results})
        return "I could not finish that question; please ask it more specifically."

    def close(self):
        self.ro.close()
