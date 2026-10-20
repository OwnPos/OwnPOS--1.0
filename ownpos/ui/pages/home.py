"""Home dashboard: today at a glance, agent findings and the latest summary."""
from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QScrollArea,
                               QTextBrowser, QVBoxLayout, QWidget)

from ...core.db import today_str
from ...core.money import fmt_money
from ..theme import pal
from ..widgets.charts import BarChart
from ..widgets.common import ActionBar, KpiTile, button, hbox, label, page_header, panel, rs, vbox

SEV_KEY = {"critical": "bad", "warning": "warn", "info": "info"}   # names in the theme palette

# The number tiles at the top of Home. The owner chooses which ones (up to MAX_TILES) and their order with
# "Choose tiles"; the choice is saved per company as the setting "home_tiles".
TILES = {
    "sales": "Sales today (all stations)", "bills": "Receipts today", "profit": "Gross profit today",
    "tax": "Sales tax today", "cash": "Cash expected in this drawer", "credit": "House charges owed",
    "stock": "Inventory value at cost", "month_sales": "Sales this month", "month_profit": "Net income this month",
    "month_expense": "Expenses this month", "purchases": "Purchases this month", "bank": "Money in bank",
    "safe": "Cash in safe", "payable": "We owe vendors", "reorder": "Items to reorder", "returns": "Returns today",
    "clocked_in": "Clocked in now",
}
DEFAULT_TILES = ["sales", "bills", "profit", "tax", "cash", "stock"]
MAX_TILES = 8


class HomePage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.setObjectName("page")
        self.win = win
        self.s = win.s
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 16, 20, 16)
        outer.setSpacing(12)
        self.title = label("", "pageTitle")
        self.sub = label("", "pageSub")
        actions = []
        for key, text, kind in (("register", "Make a Sale", "primary"), ("inventory", "Receive Inventory", ""),
                                ("items", "Item List", ""), ("eod", "End of Day", ""), ("reports", "Reports", "")):
            if key in win.nav_buttons:
                actions.append(button(text, kind, lambda _=False, k=key: win.go(k)))
        self.sub.setWordWrap(True)
        head = QHBoxLayout()
        head.setSpacing(16)
        head.addLayout(vbox(self.title, self.sub, spacing=2), 1)
        head.addWidget(ActionBar(actions), 0, Qt.AlignTop)
        outer.addLayout(head)

        if win.user.can("settings"):
            head.addWidget(button("Choose tiles", slot=self.choose_tiles), 0, Qt.AlignTop)
        # QuickBooks-style Navigator: the store's daily workflow as clickable steps
        nav = panel()
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(12, 8, 12, 8)
        nl.setSpacing(6)
        flows = [("INVENTORY", [("inventory", "Receive"), ("items", "Item List"), ("inventory", "Count")]),
                 ("SALES", [("register", "Make a Sale"), ("register", "Returns"), ("eod", "End of Day")]),
                 ("PEOPLE", [("customers", "Customers"), ("timeclock", "Time Clock"), ("employees", "Employees")])]
        for head_text, steps in flows:
            nl.addWidget(label(head_text, "navGroup"))
            for i, (key, text) in enumerate(steps):
                if key in win.nav_buttons:
                    if i:
                        nl.addWidget(label("→", "muted"))
                    nl.addWidget(button(text, slot=lambda _=False, k=key: win.go(k)))
            nl.addSpacing(18)
        nl.addStretch(1)
        outer.addWidget(nav)
        self.kpis = QGridLayout()
        self.kpis.setSpacing(10)
        self.tiles: dict[str, KpiTile] = {}
        self.kpi_tiles = ()
        self._kpi_compact = None
        self._build_tiles()
        outer.addLayout(self.kpis)

        mid = QHBoxLayout()
        mid.setSpacing(12)
        self.hours = BarChart("Today by hour")
        self.week = BarChart("Last 14 days", highlight_last=True)
        for ch in (self.hours, self.week):
            p = panel()
            pl = QVBoxLayout(p)
            pl.setContentsMargins(4, 4, 4, 4)
            pl.addWidget(ch)
            mid.addWidget(p, 1)
        outer.addLayout(mid, 2)

        low = QHBoxLayout()
        low.setSpacing(12)
        fp = panel()
        fl = QVBoxLayout(fp)
        fl.setContentsMargins(14, 12, 14, 12)
        self.f_title = label("Agent findings", "sectionTitle")
        self.findings = QListWidget()
        self.findings.setWordWrap(True)
        self.findings.setSpacing(2)
        from PySide6.QtWidgets import QAbstractItemView
        self.findings.setSelectionMode(QAbstractItemView.ExtendedSelection)
        fl.addLayout(hbox(self.f_title, None, button("Mark selected done", slot=self.ack),
                          button("Run checks now", slot=self.run_checks)))
        fl.addWidget(self.findings, 1)
        low.addWidget(fp, 3)
        sp = panel()
        sl = QVBoxLayout(sp)
        sl.setContentsMargins(14, 12, 14, 12)
        sl.addWidget(label("Latest day summary", "sectionTitle"))
        self.summary = QTextBrowser()
        self.summary.setStyleSheet("border:none")
        sl.addWidget(self.summary, 1)
        low.addWidget(sp, 2)
        outer.addLayout(low, 3)

    def _tile_keys(self) -> list[str]:
        keys = [k for k in (self.s.db.get_setting("home_tiles") or DEFAULT_TILES) if k in TILES]
        return keys[:MAX_TILES] or DEFAULT_TILES

    def _build_tiles(self):
        while self.kpis.count():
            w = self.kpis.takeAt(0).widget()
            if w:
                w.deleteLater()
        for i in range(self.kpis.columnCount()):
            self.kpis.setColumnStretch(i, 0)
        self.tiles = {}
        keys = self._tile_keys()
        per_row = 4 if len(keys) > 6 else len(keys)
        for i, key in enumerate(keys):
            t = KpiTile(TILES[key])
            self.tiles[key] = t
            self.kpis.addWidget(t, i // per_row, i % per_row)
            self.kpis.setColumnStretch(i % per_row, 1)
        self.kpi_tiles = tuple(self.tiles.values())
        self._kpi_compact = None

    def choose_tiles(self):
        from ..dialogs import BaseDialog
        from PySide6.QtWidgets import QListWidget, QListWidgetItem as Item
        d = BaseDialog(self, "Choose the Home tiles", 460)
        lst = QListWidget()
        lst.setDragDropMode(QListWidget.InternalMove)
        chosen = self._tile_keys()
        for key in chosen + [k for k in TILES if k not in chosen]:
            it = Item(TILES[key])
            it.setData(Qt.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if key in chosen else Qt.Unchecked)
            lst.addItem(it)
        d.lay.addWidget(label(f"Tick up to {MAX_TILES} tiles and drag them into the order you want.", "muted"))
        d.lay.addWidget(lst, 1)
        d.buttons("Save")
        d.resize(460, 560)
        if not d.exec():
            return
        keys = [lst.item(i).data(Qt.UserRole) for i in range(lst.count()) if lst.item(i).checkState() == Qt.Checked]
        self.s.db.set_setting("home_tiles", keys[:MAX_TILES] or DEFAULT_TILES)
        self._build_tiles()
        self.on_show()

    def _tile_values(self, key: str):
        """(big number, small line) for one tile."""
        from ...core.ledger import Ledger
        db = self.s.db
        today = date.fromisoformat(today_str())
        d = today.isoformat()
        if key in ("sales", "bills", "profit", "tax"):
            row = db.one("SELECT COUNT(CASE WHEN doc_type='INV' THEN 1 END) n,"
                         " COALESCE(SUM(CASE WHEN doc_type='INV' THEN total ELSE -total END),0) t,"
                         " COALESCE(SUM(CASE WHEN doc_type='INV' THEN tax ELSE -tax END),0) tax,"
                         " COALESCE(SUM(CASE WHEN doc_type='INV' THEN taxable+nontaxable-cost_total"
                         " ELSE -(taxable+nontaxable-cost_total) END),0) gp FROM sales WHERE sale_date = ?", (d,))
            if key == "sales":
                last_week = db.val("SELECT COALESCE(SUM(CASE WHEN doc_type='INV' THEN total ELSE -total END),0)"
                                   " FROM sales WHERE sale_date = ?", ((today - timedelta(days=7)).isoformat(),), 0)
                return rs(row["t"]), f"Same day last week {rs(last_week)}"
            if key == "bills":
                return f"{row['n']}", f"Average {rs(row['t'] // row['n'] if row['n'] else 0)}"
            if key == "tax":
                return rs(row["tax"]), "Charged on today's receipts"
            return rs(row["gp"]), "Net sales minus cost"
        if key == "cash":
            sh = self.s.shifts.current()
            if sh:
                return rs(self.s.shifts.summary(sh["id"])["expected_cash"]), f"Drawer since {sh['opened_at'][11:16]}"
            return "—", "Drawer not open"
        if key == "clocked_in":
            rows = self.s.timeclock.who_is_in()
            return f"{len(rows)}", ", ".join(r["full_name"].split()[0] for r in rows[:4])
        if key == "credit":
            owed = db.val("SELECT COALESCE(SUM(debit - credit),0) FROM journal_lines WHERE account = '1200'", default=0)
            n = db.val("SELECT COUNT(*) FROM (SELECT party_id FROM journal_lines WHERE account = '1200'"
                       " GROUP BY party_id HAVING SUM(debit-credit) > 0) x", default=0)
            return rs(owed), f"{n} customer(s)"
        if key == "stock":
            val = sum(v["value"] for v in self.s.inventory.valuation())
            low = db.val("SELECT COUNT(*) FROM items WHERE active = 1 AND reorder_point > 0 AND on_hand <= reorder_point",
                         default=0)
            return rs(val), f"{low} item(s) at reorder level"
        if key in ("month_sales", "month_profit", "month_expense", "purchases"):
            a = today.replace(day=1)
            pl = Ledger(db).profit_and_loss(a.isoformat(), d)
            if key == "month_sales":
                return rs(pl["total_income"]), "Net of returns, before tax"
            if key == "month_profit":
                return rs(pl["net_profit"]), f"Gross profit {rs(pl['gross_profit'])}"
            if key == "month_expense":
                return rs(pl["total_expense"]), "From the accounts"
            p = db.val("SELECT COALESCE(SUM(total),0) FROM purchases WHERE bill_date BETWEEN ? AND ?",
                       (a.isoformat(), d), 0)
            return rs(p), "Vendor invoices received"
        if key in ("bank", "safe", "payable"):
            led = Ledger(db)
            if key == "bank":
                total = sum(led.balance(r["code"]) for r in db.all("SELECT code FROM accounts WHERE subtype = 'bank'"
                                                                    " AND active = 1"))
                return rs(total), "Bank accounts and deposits on the way"
            if key == "payable":
                return rs(led.balance("2000")), "Vendor bills not yet paid"
            return rs(led.balance("1010")), "Account 1010"
        if key == "reorder":
            n = db.val("SELECT COUNT(*) FROM items WHERE active = 1 AND reorder_point > 0 AND on_hand <= reorder_point",
                       default=0)
            return f"{n}", "At or below their reorder point"
        if key == "returns":
            row = db.one("SELECT COUNT(*) n, COALESCE(SUM(total),0) t FROM sales WHERE doc_type = 'CN' AND sale_date = ?",
                         (d,))
            return rs(row["t"]), f"{row['n']} return(s) / void(s)"
        return "", ""

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        # on 1366 px screens the six tiles stay in one row with slightly smaller figures
        compact = self.width() < 1250
        if compact != self._kpi_compact:
            self._kpi_compact = compact
            for k in self.kpi_tiles:
                k.value.setStyleSheet("font-size: 14pt;" if compact else "")

    def on_show(self):
        db = self.s.db
        today = date.fromisoformat(today_str())
        st = db.settings()
        hour = __import__("ownpos.core.db", fromlist=["now_dt"]).now_dt().hour
        hello = "Good morning" if hour < 12 else "Good afternoon" if hour < 17 else "Good evening"
        self.title.setText(f"{hello}, {self.win.user.full_name.split()[0]}")
        self.sub.setText(f"{today:%A, %B %d, %Y} · {st.get('company_name', '')} · {self.win.station_name}")
        d = today.isoformat()
        for key, tile in self.tiles.items():
            tile.set(*self._tile_values(key))

        hours = {r["h"]: r["t"] for r in db.all(
            "SELECT CAST(substr(created_at, 12, 2) AS INTEGER) h, SUM(CASE WHEN doc_type='INV' THEN total ELSE -total END) t"
            " FROM sales WHERE sale_date = ? GROUP BY 1", (d,))}
        lo = min([9] + list(hours))
        hi = max([22] + list(hours))
        self.hours.set_data([(f"{(h % 12) or 12}{'a' if h < 12 else 'p'}", hours.get(h, 0)) for h in range(lo, hi + 1)])
        start = today - timedelta(days=13)
        per_day = {r["sale_date"]: r["t"] for r in db.all(
            "SELECT sale_date, COALESCE(SUM(CASE WHEN doc_type='INV' THEN total ELSE -total END),0) t FROM sales"
            " WHERE sale_date BETWEEN ? AND ? GROUP BY sale_date", (start.isoformat(), d))}
        days = [(f"{(start + timedelta(days=i)):%m/%d}", per_day.get((start + timedelta(days=i)).isoformat(), 0))
                for i in range(14)]
        self.week.set_data(days)

        self.findings.clear()
        rows = self.s.agents.open_findings(60)
        self.f_title.setText(f"Agent findings ({len(rows)})")
        for f in rows:
            it = QListWidgetItem(f"●  {f['title']}\n     {f['detail'] or ''}")
            it.setForeground(Qt.black)
            it.setData(Qt.UserRole, f["id"])
            from PySide6.QtGui import QColor
            it.setForeground(QColor(pal()[SEV_KEY.get(f["severity"], "info")]))
            self.findings.addItem(it)
        if not rows:
            self.findings.addItem("Nothing needs attention right now.")
        s = db.one("SELECT * FROM summaries ORDER BY id DESC LIMIT 1")
        self.summary.setPlainText(s["text"] if s else "A summary is written automatically at each Day Closing.")

    def ack(self):
        for it in self.findings.selectedItems():
            fid = it.data(Qt.UserRole)
            if fid:
                self.s.agents.acknowledge(fid, self.win.user.id)
        self.on_show()

    def run_checks(self):
        self.s.agents.run_schedule("startup")
        self.on_show()
