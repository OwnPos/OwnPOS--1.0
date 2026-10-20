"""Agents: automatic helpers that watch the books and flag problems.

Kinds:
  helper      - rule-based checks (reorder, margins, stock vs. books, sales tax due, backups)
  supervisor  - checks every receipt as it is saved (below cost, big discounts, many returns)
  builder     - writes report workbooks automatically (each day close, each month end)
  summary     - writes a plain-language summary of the day

Findings appear on the Home screen and the Agents screen until someone marks them done.

Several stations: scheduled agents run on ONE station only (Settings > Agents > "Run agents on station",
default 1, normally the server PC), so they never draft the same purchase order twice. The supervisor's
receipt check runs on whichever station saved the receipt."""
from __future__ import annotations

import traceback
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .db import now_dt, now_str, today_str
from .money import fmt_money, fmt_qty


@dataclass
class Finding:
    severity: str        # info / warning / critical
    title: str
    detail: str = ""
    entity: str | None = None
    entity_id: str | None = None
    fingerprint: str | None = None


def _today() -> date:
    return date.fromisoformat(today_str())


class Agent:
    name = "agent"
    title = "Agent"
    kind = "helper"
    schedules: tuple = ("startup",)
    description = ""

    def __init__(self, s):
        self.s = s
        self.db = s.db

    def run(self) -> tuple[str, list[Finding]]:
        raise NotImplementedError


# ---------------------------------------------------------------- helpers
class ReorderAgent(Agent):
    name, title, kind = "reorder", "Reorder helper", "helper"
    schedules = ("startup", "day_close")
    description = "Finds items at or below their reorder point and drafts one purchase order per vendor."

    def run(self):
        low = self.db.dicts("SELECT * FROM items WHERE active = 1 AND reorder_point > 0 AND on_hand <= reorder_point"
                            " AND kind = 'inventory' ORDER BY vendor_id, name")
        if not low:
            return "All items are above their reorder points.", []
        on_order = {r["item_id"] for r in self.db.all(
            "SELECT pl.item_id FROM po_lines pl JOIN purchase_orders po ON po.id = pl.po_id"
            " WHERE po.status IN ('draft','ordered')")}
        by_vendor: dict = {}
        for it in low:
            if it["id"] in on_order:
                continue
            qty = max((it["reorder_qty"] or it["reorder_point"] * 2) - it["on_hand"], 1)
            by_vendor.setdefault(it["vendor_id"], []).append({"item_id": it["id"], "qty": round(qty, 3),
                                                              "est_cost": it["last_cost"], "name": it["name"]})
        findings = []
        for vendor_id, lines in by_vendor.items():
            po_id = self.s.purchasing.create_po(vendor_id, lines, None, "Drafted by the reorder agent", by_agent=True)
            vname = self.db.val("SELECT name FROM vendors WHERE id = ?", (vendor_id,), "no vendor set")
            names = ", ".join(ln["name"] for ln in lines[:5]) + (" ..." if len(lines) > 5 else "")
            findings.append(Finding("warning", f"Draft purchase order for {vname}",
                                    f"{len(lines)} item(s) low: {names}. Review it in Receive & Adjust > Purchase orders.",
                                    "purchase_order", str(po_id), f"po:{po_id}"))
        return f"{len(low)} item(s) low; {len(by_vendor)} draft PO(s) created.", findings


class MarginAgent(Agent):
    name, title, kind = "margin", "Margin guard", "helper"
    schedules = ("startup", "day_close")
    description = "Warns when an item's price is below its cost or under its department's target margin."

    def run(self):
        rows = self.db.dicts(
            "SELECT i.id, i.name, i.size, i.price1, i.cost_avg, COALESCE(d.margin_target,0) AS target FROM items i"
            " LEFT JOIN departments d ON d.id = i.department_id"
            " WHERE i.active = 1 AND i.kind = 'inventory' AND i.cost_avg > 0 AND i.price1 > 0")
        findings = []
        for r in rows:
            margin = (r["price1"] - r["cost_avg"]) / r["price1"] * 100
            name = f"{r['name']} {r['size'] or ''}".strip()
            if r["cost_avg"] > r["price1"]:
                findings.append(Finding("critical", f"{name} sells below cost",
                                        f"Price ${fmt_money(r['price1'])}, average cost ${fmt_money(round(r['cost_avg']))}.",
                                        "item", str(r["id"]), f"margin:{r['id']}:below:{r['price1']}"))
            elif r["target"] and margin < r["target"] - 0.5:
                findings.append(Finding("info", f"{name} margin {margin:.0f}% (target {r['target']:.0f}%)",
                                        "Consider a price review.", "item", str(r["id"]),
                                        f"margin:{r['id']}:target:{r['price1']}"))
        return f"Checked {len(rows)} priced items.", findings


class InventorySanityAgent(Agent):
    name, title, kind = "inventory", "Inventory check", "helper"
    schedules = ("day_close",)
    description = "Flags negative stock and any difference between the stock list and the Inventory account."

    def run(self):
        findings = []
        neg = self.db.dicts("SELECT id, name, on_hand, unit FROM items WHERE active = 1 AND on_hand < 0"
                            " AND kind = 'inventory' ORDER BY name")
        for r in neg[:50]:
            findings.append(Finding("warning", f"{r['name']} shows negative stock ({fmt_qty(r['on_hand'])} {r['unit']})",
                                    "More was sold than was received. Receive the missing invoice or do a count.",
                                    "item", str(r["id"]), f"neg:{r['id']}"))
        val = sum(v["value"] for v in self.s.inventory.valuation())
        led = self.s.ledger.balance("1300")
        diff = led - val
        if abs(diff) > 100:
            findings.append(Finding("warning", "Stock value and Inventory account differ",
                                    f"Stock list ${fmt_money(val)}, Inventory account ${fmt_money(led)} "
                                    f"(difference ${fmt_money(diff)}).", None, None, f"invdiff:{today_str()}"))
        return f"{len(neg)} negative item(s); stock value ${fmt_money(val)}.", findings


class SalesTaxAgent(Agent):
    name, title, kind = "sales_tax", "Sales tax reminder", "helper"
    schedules = ("startup",)
    description = ("Works out the last filing period's Indiana sales tax and reminds you before the due date "
                   "(the 20th of the month after the period).")

    def run(self):
        freq = self.db.get_setting("sales_tax_frequency", "quarterly")
        today = _today()
        if freq == "monthly":
            end = today.replace(day=1) - timedelta(days=1)
            start = end.replace(day=1)
            label = start.strftime("%B %Y")
        elif freq == "annual":
            start, end = date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
            label = str(today.year - 1)
        else:
            q_start = date(today.year, (today.month - 1) // 3 * 3 + 1, 1)
            end = q_start - timedelta(days=1)
            start = date(end.year, (end.month - 1) // 3 * 3 + 1, 1)
            label = f"Q{(start.month - 1) // 3 + 1} {start.year}"
        due = date(end.year + (end.month == 12), end.month % 12 + 1, 20) if freq != "annual" else date(today.year, 1, 31)
        from .reports import run_report
        res = run_report(self.db, "sales_tax_liability", {"date_from": start.isoformat(), "date_to": end.isoformat()})
        total = next((r for r in res.rows if r.get("month") == "Period total"), None)
        tax = total["collected"] if total else 0
        days_left = (due - today).days
        if days_left < 0:
            return f"{label}: due date {due:%b %d, %Y} has passed.", []
        sev = "warning" if days_left <= 7 else "info"
        return f"{label}: sales tax ${fmt_money(tax)}.", [
            Finding(sev, f"Sales tax for {label}: ${fmt_money(tax)}",
                    f"File and pay on INTIME by {due:%A, %B %d, %Y} ({days_left} day(s) left). "
                    "Figures: Reports > Sales Tax > Sales Tax Liability.", None, None, f"stax:{label}")]


class BackupAgent(Agent):
    name, title, kind = "backup", "Backup guard", "helper"
    schedules = ("startup", "day_close", "hourly")
    description = "Backs up the store database at each day close and whenever the last backup is over 24 hours old."

    def run(self):
        from .backup import backup_all, BackupError
        last = self.db.get_setting("last_backup_at")
        findings = []
        need = getattr(self, "_trigger", "") == "day_close" or not last
        if not need:
            age = now_dt() - datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
            need = age > timedelta(hours=24)
        if need:
            try:
                paths = backup_all(self.db, self.s.conninfo if hasattr(self.s, "conninfo") else None, "auto")
            except BackupError as e:
                return f"Backup failed: {e}", [Finding("critical", "Backup failed", str(e), None, None,
                                                       f"backupfail:{today_str()}")]
            summary = f"Backed up to {paths[0]}"
        else:
            summary = f"Last backup {last}."
        if not (self.db.get_setting("backup_extra_folder") or ""):
            findings.append(Finding("info", "Set a second backup folder",
                                    "Choose a USB drive or a cloud-synced folder in Settings > Backups so a copy lives "
                                    "off the server PC.", None, None, "backup:noextra"))
        return summary, findings


class IntegrityAgent(Agent):
    name, title, kind = "integrity", "Books integrity check", "helper"
    schedules = ("startup", "day_close")
    description = "Confirms debits equal credits, the balance sheet balances, and the audit log has not been altered."

    def run(self):
        from .auth import verify_audit_chain
        findings = []
        if not self.s.ledger.is_balanced():
            findings.append(Finding("critical", "Ledger does not balance", "Contact support before posting more entries.",
                                    None, None, "integrity:ledger"))
        if not self.s.ledger.balance_sheet(today_str())["balanced"]:
            findings.append(Finding("critical", "Balance sheet does not balance", "", None, None, "integrity:bs"))
        ok, bad = verify_audit_chain(self.db)
        if not ok:
            findings.append(Finding("critical", "Audit log has been altered", f"The chain breaks at entry {bad}.",
                                    "audit_log", str(bad), "integrity:audit"))
        bad_receipts = self.db.val(
            "SELECT COUNT(*) FROM sales s WHERE s.total <> (SELECT COALESCE(SUM(l.total),0) FROM sale_lines l"
            " WHERE l.sale_id = s.id)", default=0)
        if bad_receipts:
            findings.append(Finding("critical", f"{bad_receipts} receipt(s) do not add up to their lines", "",
                                    None, None, "integrity:receipts"))
        return "Books balanced and audit log intact." if not findings else "Problems found.", findings


# ---------------------------------------------------------------- supervisor
class TransactionSupervisor(Agent):
    name, title, kind = "supervisor", "Transaction supervisor", "supervisor"
    schedules = ("realtime", "day_close")
    description = ("Checks every receipt as it is saved: sold below cost, discounts over the cashier limit, "
                   "and unusual returns, voids, reprints or drawer openings per cashier.")

    def check_sale(self, sale_id: int) -> list[Finding]:
        s = self.db.one("SELECT s.*, u.full_name FROM sales s JOIN users u ON u.id = s.user_id WHERE s.id = ?", (sale_id,))
        if s is None:
            return []
        out = []
        if s["doc_type"] == "INV":
            for ln in self.db.all("SELECT * FROM sale_lines WHERE sale_id = ?", (sale_id,)):
                if ln["cost_total"] and ln["net"] < ln["cost_total"]:
                    out.append(Finding("warning", f"Sold below cost on receipt #{s['receipt_no']}",
                                       f"{ln['description']}: sold for ${fmt_money(ln['net'])}, cost "
                                       f"${fmt_money(ln['cost_total'])}. Associate: {s['full_name']}.",
                                       "sale", str(sale_id), f"belowcost:{ln['id']}"))
            limit = float(self.db.get_setting("max_discount_pct_cashier", 20) or 20)
            if s["subtotal"] and s["discount"] / s["subtotal"] * 100 > limit + 0.01:
                out.append(Finding("warning", f"Discount of {s['discount'] / s['subtotal'] * 100:.0f}% on receipt "
                                              f"#{s['receipt_no']}",
                                   f"${fmt_money(s['discount'])} off. Associate: {s['full_name']}.",
                                   "sale", str(sale_id), f"disc:{sale_id}"))
        else:
            out.append(Finding("info", f"{'Void' if s['notes'] == 'Void' else 'Return'} #{s['receipt_no']}",
                               f"${fmt_money(s['total'])} refunded. Reason: {s['reason']}. By {s['full_name']}.",
                               "sale", str(sale_id), f"cn:{sale_id}"))
        return out

    def run(self):
        day = today_str()
        findings = []
        for r in self.db.dicts(
                "SELECT u.full_name, COUNT(*) n, SUM(s.total) amt FROM sales s JOIN users u ON u.id = s.user_id"
                " WHERE s.doc_type = 'CN' AND s.sale_date = ? GROUP BY u.id, u.full_name HAVING COUNT(*) >= 3", (day,)):
            findings.append(Finding("warning", f"{r['full_name']} made {r['n']} returns/voids today",
                                    f"Total ${fmt_money(r['amt'])}. Check Reports > Sales > Returns and Voids.", None, None,
                                    f"cnmany:{day}:{r['full_name']}"))
        for r in self.db.dicts("SELECT u.full_name, COUNT(*) n FROM cash_moves m JOIN users u ON u.id = m.user_id"
                               " WHERE m.kind = 'no_sale' AND substr(m.created_at, 1, 10) = ? GROUP BY u.id, u.full_name"
                               " HAVING COUNT(*) >= 5", (day,)):
            findings.append(Finding("warning", f"{r['full_name']} opened the drawer {r['n']} times without a sale",
                                    "", None, None, f"nosale:{day}:{r['full_name']}"))
        rp = self.db.val("SELECT COUNT(*) FROM print_log WHERE copy_no > 0 AND substr(ts, 1, 10) = ?", (day,), 0)
        if rp >= 5:
            findings.append(Finding("info", f"{rp} receipt reprints today", "See Reports > Accountant > Receipt Reprints.",
                                    None, None, f"reprints:{day}"))
        over = self.db.dicts("SELECT details FROM audit_log WHERE action = 'sale' AND substr(ts, 1, 10) = ?"
                             " AND details LIKE ?", (day, '%hours_override%'))
        if over:
            findings.append(Finding("info", f"{len(over)} sale(s) made outside the store's sale hours",
                                    "The cashier continued past the sale-hours warning. Check the PC clocks and the "
                                    "Audit Trail.", None, None, f"hours:{day}"))
        return "Reviewed today's returns, drawer openings, reprints and sale hours.", findings


# ---------------------------------------------------------------- builders & summary
class ReportBuilderAgent(Agent):
    name, title, kind = "reports", "Report builder", "builder"
    schedules = ("day_close", "monthly")
    description = "Writes a day workbook at each day close and a month-end workbook after each month."

    def folder(self) -> Path:
        f = self.db.get_setting("report_folder") or ""
        if f:
            return Path(f)
        from .paths import data_root
        return data_root() / "reports"

    def run(self):
        made = [self.build_day(_today())]
        first_this = _today().replace(day=1)
        prev_end = first_this - timedelta(days=1)
        a = prev_end.replace(day=1)
        key = f"month_book_{a.isoformat()}"
        if not self.db.get_setting(key) and self.db.val("SELECT 1 FROM sales WHERE sale_date BETWEEN ? AND ? LIMIT 1",
                                                        (a.isoformat(), prev_end.isoformat())):
            made.append(self.build_period(a, prev_end))
            self.db.set_setting(key, now_str())
        return "Wrote " + ", ".join(p.name for p in made), []

    def _book(self, path: Path, sheets: list[tuple[str, dict]]) -> Path:
        from .excel import export_book
        from .reports import run_report
        results = [run_report(self.db, key, params) for key, params in sheets]
        return export_book(path, results, self.db.get_setting("company_name", ""))

    def build_day(self, d: date) -> Path:
        p = {"date_from": d.isoformat(), "date_to": d.isoformat()}
        path = self.folder() / "daily" / f"Day_{d.isoformat()}.xlsx"
        return self._book(path, [("sales_summary", p), ("sales_by_department", p), ("sales_by_item", p),
                                 ("tenders", p), ("sales_by_associate", p), ("sales_by_hour", p), ("discounts", p),
                                 ("returns", p), ("z_outs", p), ("cash_moves", p), ("waste", p),
                                 ("sales_tax_daily", p)])

    def build_period(self, a: date, b: date) -> Path:
        p = {"date_from": a.isoformat(), "date_to": b.isoformat()}
        path = self.folder() / "monthly" / f"Month_{a:%Y-%m}.xlsx"
        return self._book(path, [("profit_loss", p), ("balance_sheet", {"date_to": b.isoformat()}),
                                 ("trial_balance", {"date_to": b.isoformat()}), ("sales_tax_liability", p),
                                 ("sales_summary", p), ("sales_by_department", p), ("sales_by_item", p),
                                 ("purchases_by_vendor", p), ("ap_aging", {"date_to": b.isoformat()}),
                                 ("ar_aging", {"date_to": b.isoformat()}), ("inventory_by_department", {}),
                                 ("payroll_hours", p), ("expenses", p)])


class SummaryAgent(Agent):
    name, title, kind = "summary", "Summary writer", "summary"
    schedules = ("day_close",)
    description = "Writes a plain-language summary of the day: sales, best sellers, payments, drawers and anything flagged."

    def run(self):
        text = self.day_summary(_today())
        self.db.insert("INSERT INTO summaries(created_at, kind, period, text) VALUES(?,?,?,?)",
                       (now_str(), "day", today_str(), text))
        return text.split("\n")[0], []

    def day_summary(self, d: date) -> str:
        ds = d.isoformat()
        dc = self.s.shifts.day_closing(ds)
        prev = self.db.val("SELECT COALESCE(SUM(CASE WHEN doc_type='INV' THEN total ELSE -total END),0) FROM sales"
                           " WHERE sale_date = ?", ((d - timedelta(days=7)).isoformat(),), 0)
        top = self.db.all("SELECT l.description, SUM(l.qty) q, SUM(l.net) t FROM sale_lines l"
                          " JOIN sales s ON s.id = l.sale_id WHERE s.sale_date = ? AND s.doc_type = 'INV'"
                          " GROUP BY l.description ORDER BY t DESC LIMIT 3", (ds,))
        wasted = self.db.val("SELECT COALESCE(SUM(value),0) FROM adjustments WHERE kind = 'waste'"
                             " AND substr(created_at, 1, 10) = ?", (ds,), 0)
        flags = self.db.val("SELECT COUNT(*) FROM agent_findings WHERE acknowledged = 0 AND severity <> 'info'", default=0)
        taken = dc["sales_total"] - dc["returns_total"]
        lines = []
        head = f"{d:%A, %B %d, %Y}: {dc['n_inv']} sales, ${fmt_money(taken)} taken"
        if prev:
            change = (taken - prev) / prev * 100
            head += f", {'up' if change >= 0 else 'down'} {abs(change):.0f}% on the same day last week"
        lines.append(head + ".")
        if dc["net"]:
            lines.append(f"Net sales ${fmt_money(dc['net'])}, sales tax ${fmt_money(dc['tax'])}, gross profit "
                         f"${fmt_money(dc['gross_profit'])} ({dc['gross_profit'] / dc['net'] * 100:.0f}%).")
        if top:
            lines.append("Best sellers: " + "; ".join(f"{r['description']} x{fmt_qty(r['q'])} (${fmt_money(r['t'])})"
                                                      for r in top) + ".")
        if dc["tenders"]:
            lines.append("Payments: " + ", ".join(f"{t['name']} ${fmt_money(t['net'])}" for t in dc["tenders"]) + ".")
        if dc["returns_total"]:
            lines.append(f"Returns: ${fmt_money(dc['returns_total'])} ({dc['n_cn']}).")
        for r in dc["discount_lines"]:
            lines.append(f"{r['reason']}: {r['n']} sale(s), ${fmt_money(r['amount'])} off.")
        if wasted:
            lines.append(f"Breakage / waste at cost: ${fmt_money(wasted)}.")
        for st in dc["stations"]:
            if st["status"] == "closed":
                os_ = st["over_short"] or 0
                lines.append(f"{st['station_name'] or 'Station ' + str(st['station_id'])} {st['z_number']}: drawer "
                             f"{'balanced' if os_ == 0 else ('over' if os_ > 0 else 'short')}"
                             f"{'' if os_ == 0 else ' by $' + fmt_money(abs(os_))}.")
        if dc["open_drawers"]:
            lines.append(f"{len(dc['open_drawers'])} drawer(s) still open.")
        if flags:
            lines.append(f"{flags} agent warning(s) are waiting on the Home screen.")
        return "\n".join(lines)


AGENT_CLASSES = [TransactionSupervisor, ReorderAgent, MarginAgent, InventorySanityAgent, SalesTaxAgent,
                 ReportBuilderAgent, SummaryAgent, BackupAgent, IntegrityAgent]


class AgentManager:
    def __init__(self, services):
        self.s = services
        self.db = services.db
        self.agents = {cls.name: cls(services) for cls in AGENT_CLASSES}

    def enabled(self, name: str) -> bool:
        return bool(self.db.get_setting(f"agent_{name}_enabled", True))

    def set_enabled(self, name: str, on: bool) -> None:
        self.db.set_setting(f"agent_{name}_enabled", bool(on))

    def is_agent_station(self) -> bool:
        """Scheduled agents run on one station only (default station 1)."""
        return int(self.db.get_setting("agent_station", 1) or 1) == (self.db.station_id or 0)

    def _store(self, agent_name: str, findings: list[Finding]) -> int:
        new = 0
        for f in findings:
            with self.db.tx():
                if f.fingerprint and self.db.one("SELECT 1 FROM agent_findings WHERE fingerprint = ? AND acknowledged = 0",
                                                 (f.fingerprint,)):
                    continue
                self.db.exec("INSERT INTO agent_findings(created_at, agent, severity, title, detail, entity, entity_id,"
                             " fingerprint) VALUES(?,?,?,?,?,?,?,?)",
                             (now_str(), agent_name, f.severity, f.title, f.detail, f.entity, f.entity_id, f.fingerprint))
            new += 1
        return new

    def run(self, name: str, trigger: str = "manual") -> str:
        agent = self.agents[name]
        agent._trigger = trigger
        rid = self.db.insert("INSERT INTO agent_runs(agent, started_at, status) VALUES(?,?,?)", (name, now_str(), "running"))
        try:
            summary, findings = agent.run()
            self._store(name, findings)
            status = "ok"
        except Exception as exc:  # an agent must never break the register
            summary, status = f"Error: {exc}", "error"
            try:
                self._store(name, [Finding("warning", f"{agent.title} could not finish", str(exc), None, None,
                                           f"agenterr:{name}:{today_str()}")])
            except Exception:  # noqa: BLE001
                pass
            traceback.print_exc()
        try:
            self.db.exec("UPDATE agent_runs SET finished_at = ?, status = ?, summary = ? WHERE id = ?",
                         (now_str(), status, summary, rid))
        except Exception:  # noqa: BLE001
            pass
        return summary

    def run_schedule(self, schedule: str, force: bool = False) -> dict:
        out = {}
        if not force and not self.is_agent_station():
            return out
        for name, agent in self.agents.items():
            if schedule in agent.schedules and self.enabled(name) and schedule != "realtime":
                if name == "backup" and not self.is_agent_station():
                    continue            # backups need PostgreSQL's tools, which live on the agent (server) station
                out[name] = self.run(name, schedule)
        return out

    def on_sale(self, sale_id: int) -> list[Finding]:
        """Real-time supervisor hook, called right after a receipt is saved."""
        if not self.enabled("supervisor"):
            return []
        try:
            findings = self.agents["supervisor"].check_sale(sale_id)
            self._store("supervisor", findings)
            return findings
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return []

    def open_findings(self, limit: int = 100) -> list[dict]:
        return self.db.dicts("SELECT * FROM agent_findings WHERE acknowledged = 0 ORDER BY"
                             " CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END, id DESC LIMIT ?",
                             (limit,))

    def acknowledge(self, finding_id: int, user_id: int) -> None:
        self.db.exec("UPDATE agent_findings SET acknowledged = 1, ack_by = ?, ack_at = ? WHERE id = ?",
                     (user_id, now_str(), finding_id))

    def last_runs(self) -> dict:
        rows = self.db.dicts("SELECT DISTINCT ON (agent) * FROM agent_runs ORDER BY agent, id DESC")
        return {r["agent"]: r for r in rows}
