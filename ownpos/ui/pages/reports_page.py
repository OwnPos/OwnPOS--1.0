"""Report Center (QuickBooks style): report groups on the left, the report on the right in an Excel-style grid
with date presets, filters, memorized reports, Excel export, Save as PDF and printing.

Dates come from the store server's clock, so every station's "Today" is the same day."""
from __future__ import annotations

import html as _html
import json
from datetime import date, timedelta

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFileDialog, QHBoxLayout, QMenu, QSpinBox, QSplitter, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from ...core.db import now_str, today_str
from ...core.reports import REGISTRY, categories_of_reports, run_report
from .. import printing
from ..widgets.common import DateEdit, RefCombo, ask_text, button, error, guarded, hbox, label, page_header
from ..widgets.grid import Col, DataGrid, display, us_date

PRESETS = ["Today", "Yesterday", "This week", "Last week", "This month", "Last month", "This quarter",
           "Last quarter", "This year", "Last year", "Last 7 days", "Last 30 days", "Custom"]


def _quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def preset_range(name: str, today: date, week_starts: int = 6) -> tuple[date, date]:
    """(first day, last day) of a date preset. week_starts: 0 = Monday ... 6 = Sunday."""
    t = today
    if name == "Yesterday":
        y = t - timedelta(days=1)
        return y, y
    if name in ("This week", "Last week"):
        start = t - timedelta(days=(t.weekday() - week_starts) % 7)
        if name == "Last week":
            start -= timedelta(days=7)
            return start, start + timedelta(days=6)
        return start, t
    if name == "This month":
        return t.replace(day=1), t
    if name == "Last month":
        end = t.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if name == "This quarter":
        return _quarter_start(t), t
    if name == "Last quarter":
        end = _quarter_start(t) - timedelta(days=1)
        return _quarter_start(end), end
    if name == "This year":
        return date(t.year, 1, 1), t
    if name == "Last year":
        return date(t.year - 1, 1, 1), date(t.year - 1, 12, 31)
    if name == "Last 7 days":
        return t - timedelta(days=6), t
    if name == "Last 30 days":
        return t - timedelta(days=29), t
    return t, t


class ReportsPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.setObjectName("page")
        self.win = win
        self.s = win.s
        self.current = None
        self.result = None
        self._item_model = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.addLayout(page_header("Report Center", "Pick a report, set the dates, then sort and filter like a "
                                  "spreadsheet. Export to Excel or save as PDF."))
        body = QSplitter(Qt.Horizontal)
        body.setChildrenCollapsible(False)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(210)
        self.tree.itemClicked.connect(self._pick)
        self.tree.currentItemChanged.connect(lambda cur, _prev: self._pick(cur, 0) if cur is not None else None)
        body.addWidget(self.tree)
        right_w = QWidget()
        right = QVBoxLayout(right_w)
        right.setContentsMargins(8, 0, 0, 0)
        self.title = label("Choose a report", "sectionTitle")
        self.preset = QComboBox()
        self.preset.addItems(PRESETS)
        self.preset.currentTextChanged.connect(self._preset)
        self.preset.activated.connect(lambda *_: self.run())
        self.d_from = DateEdit()
        self.d_to = DateEdit()
        self._setting_preset = False
        for d in (self.d_from, self.d_to):
            d.changed.connect(self._dates_typed)
        self.dept = RefCombo([], blank="All departments")
        from ..widgets.lines import ItemCombo
        self.item = ItemCombo([])
        self.item.setMinimumWidth(320)
        self.item.setToolTip("Type an item #, name or UPC")
        self.user = RefCombo([], blank="All employees")
        self.account = RefCombo([])
        self.days = QSpinBox()
        self.days.setRange(1, 730)
        self.days.setValue(60)
        self.days.setSuffix(" days")
        self.params_row = QHBoxLayout()
        self.lbl_from, self.lbl_to = label("From"), label("To")
        for w in (self.preset, self.lbl_from, self.d_from, self.lbl_to, self.d_to):
            self.params_row.addWidget(w)
        self.params_row.addStretch(1)
        self.filters_row = QHBoxLayout()
        for w in (self.dept, self.item, self.user, self.account, self.days):
            self.filters_row.addWidget(w)
        self.filters_row.addStretch(1)
        self.run_btn = button("Run report", "primary", self.run)
        more = button("More ▾")
        m = QMenu(more)
        m.addAction("Print…", self.print_)
        m.addAction("Memorize this report…", self.memorize)
        m.addAction("Remove memorized report", self.forget)
        more.setMenu(m)
        self.title.setWordWrap(True)
        actions = [self.run_btn]
        if win.user.can("export"):
            actions += [button("Export to Excel", slot=self.export), button("Save as PDF", slot=self.save_pdf)]
        right.addLayout(hbox(self.title, None, *actions, more))
        right.addLayout(self.params_row)
        right.addLayout(self.filters_row)
        self.status = label("Click a report on the left. It runs at once; change the dates and press Run report "
                            "to run it again.", "muted", wrap=True)
        right.addWidget(self.status)
        self.notes = label("", "muted", wrap=True)
        right.addWidget(self.notes)
        self.grid = DataGrid([], key="report", toolbar=True)
        right.addWidget(self.grid, 1)
        body.addWidget(right_w)
        body.setStretchFactor(1, 1)
        body.setSizes([270, 900])
        lay.addWidget(body, 1)
        self._load_filters()
        self._build_tree()
        self.preset.setCurrentText("This month")
        self._preset("This month")
        self._show_params([])

    # ---- lists ---------------------------------------------------------
    def _load_filters(self):
        db = self.s.db
        self.dept.set_choices([(r["id"], r["name"]) for r in db.all("SELECT id, name FROM departments ORDER BY sort, name")],
                              blank="All departments")
        self.user.set_choices([(r["id"], r["full_name"]) for r in db.all("SELECT id, full_name FROM users ORDER BY full_name")],
                              blank="All employees")
        self.account.set_choices([(r["code"], f"{r['code']} {r['name']}") for r in db.all(
            "SELECT code, name FROM accounts ORDER BY code")])

    def _ensure_items(self):
        """The item picker is filled the first time a report needs it (20,000 items)."""
        if self._item_model is None:
            from ..widgets.lines import item_model
            self._item_model = item_model(self.s.db.dicts(
                "SELECT id, item_no, name, size, upc FROM items ORDER BY name"))
            self.item.setModel(self._item_model)
            self.item.setCurrentIndex(0)

    def _allowed(self, rd) -> bool:
        return self.win.user.can(rd.perm)

    def _build_tree(self):
        self.tree.blockSignals(True)          # rebuilding the list must not run reports
        try:
            self._fill_tree()
        finally:
            self.tree.blockSignals(False)

    def _fill_tree(self):
        self.tree.clear()
        mem = [m for m in self.s.db.all("SELECT * FROM memorized_reports ORDER BY name")
               if m["report_key"] in REGISTRY and self._allowed(REGISTRY[m["report_key"]])]
        if mem:
            top = QTreeWidgetItem(["★ Memorized"])
            for m in mem:
                it = QTreeWidgetItem([m["name"]])
                it.setData(0, Qt.UserRole, ("mem", m["id"]))
                top.addChild(it)
            self.tree.addTopLevelItem(top)
            top.setExpanded(True)
        for cat, reps in categories_of_reports().items():
            reps = [rd for rd in reps if self._allowed(rd)]
            if not reps:
                continue
            top = QTreeWidgetItem([cat])
            for rd in reps:
                it = QTreeWidgetItem([rd.title])
                it.setData(0, Qt.UserRole, ("rep", rd.key))
                if rd.description:
                    it.setToolTip(0, rd.description)
                top.addChild(it)
            self.tree.addTopLevelItem(top)
            top.setExpanded(cat in ("Sales", "Sales Tax"))

    def on_show(self):
        self._load_filters()
        self._build_tree()

    # ---- parameters --------------------------------------------------
    def _today(self) -> date:
        return date.fromisoformat(today_str())

    def _dates_typed(self):
        """Dates changed by hand: the preset becomes Custom."""
        if not self._setting_preset and self.preset.currentText() != "Custom":
            self.preset.blockSignals(True)
            self.preset.setCurrentText("Custom")
            self.preset.blockSignals(False)

    def _preset(self, name):
        if name == "Custom":
            return
        ws = self.s.db.get_setting("week_starts", 6)
        week = 6 if ws in (None, "") else int(ws)         # 0 = Monday is a real value
        a, b = preset_range(name, self._today(), week)
        self._setting_preset = True
        try:
            self.d_from.set_date(a)
            self.d_to.set_date(b)
        finally:
            self._setting_preset = False
        if self.preset.currentText() != name:
            self.preset.blockSignals(True)
            self.preset.setCurrentText(name)
            self.preset.blockSignals(False)

    def _show_params(self, params):
        has_range = "date_range" in params
        only_to = "date_to" in params
        self.preset.setVisible(has_range)
        self.lbl_from.setVisible(has_range)
        self.d_from.setVisible(has_range)
        self.lbl_to.setText("To" if has_range else "As of")
        self.lbl_to.setVisible(has_range or only_to)
        self.d_to.setVisible(has_range or only_to)
        self.dept.setVisible("department" in params)
        self.item.setVisible("item" in params)
        if "item" in params:
            self._ensure_items()
        self.user.setVisible("user" in params)
        self.account.setVisible("account" in params)
        self.days.setVisible("days" in params)

    def _pick(self, it, _col):
        import time
        data = it.data(0, Qt.UserRole)
        if not data:
            return
        now = time.monotonic()            # a mouse click both moves the selection and clicks: run once
        if getattr(self, "_last_pick", None) and self._last_pick[0] == data and now - self._last_pick[1] < 0.6:
            return
        self._last_pick = (data, now)
        kind, ref = data
        if kind == "mem":
            m = self.s.db.one("SELECT * FROM memorized_reports WHERE id = ?", (ref,))
            if m is None:
                return
            self.current = REGISTRY[m["report_key"]]
            self._mem_id = m["id"]
            p = json.loads(m["params_json"])
            self._show_params(self.current.params)
            if p.get("preset") and p["preset"] != "Custom":
                self._preset(p["preset"])
            else:
                if p.get("date_from"):
                    self.d_from.set_date(date.fromisoformat(p["date_from"]))
                if p.get("date_to"):
                    self.d_to.set_date(date.fromisoformat(p["date_to"]))
                self._dates_typed()
            for key, w in (("department_id", self.dept), ("user_id", self.user), ("account", self.account)):
                if p.get(key):
                    w.set_value(p[key])
            if p.get("item_id"):
                self._ensure_items()
                self.item.set_value(p["item_id"])
            if p.get("days"):
                self.days.setValue(int(p["days"]))
            self.title.setText(m["name"])
        else:
            self._mem_id = None
            self.current = REGISTRY[ref]
            self.title.setText(self.current.title)
            self._show_params(self.current.params)
        self.run()

    def params(self) -> dict:
        p = {}
        if not self.current:
            return p
        cp = self.current.params
        if "date_range" in cp:
            p["date_from"], p["date_to"] = self.d_from.iso(), self.d_to.iso()
        if "date_to" in cp:
            p["date_to"] = self.d_to.iso()
        if "department" in cp and self.dept.value():
            p["department_id"] = self.dept.value()
        if "item" in cp and self.item.value():
            p["item_id"] = self.item.value()
        if "user" in cp and self.user.value():
            p["user_id"] = self.user.value()
        if "account" in cp:
            p["account"] = self.account.value()
        if "days" in cp:
            p["days"] = self.days.value()
        return p

    # ---- running -----------------------------------------------------
    @guarded
    def run(self):
        if not self.current:
            return
        p = self.params()
        if p.get("date_from") and p.get("date_to") and p["date_from"] > p["date_to"]:
            error(self, "The From date is after the To date")
            return
        self.result = run_report(self.s.db, self.current.key, p)
        totals = getattr(self.result, "totals", True)
        cols = [Col(k, l, t, width=260 if k == "line" else None, total=None if totals else False)
                for k, l, t in self.result.columns]
        self.grid.show_totals = bool(totals)
        self.grid.set_columns(cols, self.result.rows)
        self.notes.setText(self.result.notes)
        self._say_result()

    def _period_text(self) -> str:
        p = self.params()
        if p.get("date_from") and p.get("date_to"):
            a, b = us_date(p["date_from"]), us_date(p["date_to"])
            return a if a == b else f"{a} to {b}"
        if p.get("date_to"):
            return f"As of {us_date(p['date_to'])}"
        return ""

    def _say_result(self):
        """Say plainly what came out, so an empty result never looks like 'nothing happened'."""
        from ...core.db import now_dt
        rows = [r for r in self.result.rows if not r.get("style")]
        when = self._period_text()
        when = f" ({when})" if when else ""
        if rows:
            self.status.setObjectName("muted")
            self.status.setText(f"{len(rows):,} row(s){when} · run at {now_dt():%I:%M %p}.")
        else:
            self.status.setObjectName("notice")
            first, last = self._data_dates()
            hint = (f" The store has entries from {us_date(first)} to {us_date(last)}: choose dates in that range "
                    "and press Run report.") if first else " The store has no entries yet."
            self.status.setText(f"No entries{when}.{hint}")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _data_dates(self) -> tuple[str | None, str | None]:
        db = self.s.db
        dates = [d for d in (db.val("SELECT MIN(sale_date) FROM sales"), db.val("SELECT MIN(entry_date) FROM journal"))
                 if d]
        ends = [d for d in (db.val("SELECT MAX(sale_date) FROM sales"), db.val("SELECT MAX(entry_date) FROM journal"))
                if d]
        return (min(dates)[:10], max(ends)[:10]) if dates and ends else (None, None)

    def _file_name(self, ext: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in self.title.text()).strip() or "Report"
        return f"{safe} {today_str()}.{ext}"

    @guarded
    def export(self):
        if not self.result:
            error(self, "Run a report first")
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export to Excel", self._file_name("xlsx"), "Excel workbook (*.xlsx)")
        if path:
            notes = "; ".join(x for x in (self._period_text(), self.result.notes) if x)
            self.grid.export_to(path, self.title.text(), self.s.db.get_setting("company_name", ""), self.result.params,
                                notes)
            self.win.toast(f"Saved {path}")

    @guarded
    def save_pdf(self):
        if not self.result:
            error(self, "Run a report first")
            return
        path = self.grid.export_pdf_dialog(self.title.text(), self.s.db.get_setting("company_name", ""),
                                           self._period_text(), self.result.notes)
        if path:
            self.win.toast(f"Saved {path}")

    @guarded
    def memorize(self):
        if not self.current:
            error(self, "Run a report first")
            return
        name = ask_text(self, "Memorize report", "Name for this report (keeps its type, date preset and filters):",
                        self.title.text())
        if not name:
            return
        p = self.params()
        p["preset"] = self.preset.currentText()
        self.s.db.insert("INSERT INTO memorized_reports(name, report_key, params_json, user_id, created_at)"
                         " VALUES(?,?,?,?,?)", (name.strip(), self.current.key, json.dumps(p), self.win.user.id,
                                                now_str()))
        self._build_tree()
        self.win.toast(f"Memorized '{name.strip()}'.")

    @guarded
    def forget(self):
        mid = getattr(self, "_mem_id", None)
        if not mid:
            error(self, "Click a memorized report (under ★ Memorized) first")
            return
        self.s.db.exec("DELETE FROM memorized_reports WHERE id = ?", (mid,))
        self._mem_id = None
        self._build_tree()

    @guarded
    def print_(self):
        if not self.result:
            error(self, "Run a report first")
            return
        cols = self.grid.visible_cols()
        rows = self.grid.visible_rows()
        h = [f"<html><body style=\"font-family:'Segoe UI',sans-serif;font-size:8pt\">"
             f"<h3>{_html.escape(self.s.db.get_setting('company_name', ''))}<br/>{_html.escape(self.title.text())}</h3>"
             f"<p>{_html.escape(self._period_text())}</p>"
             f"<table border='1' cellspacing='0' cellpadding='3' width='100%'><tr>"]
        h += [f"<th>{_html.escape(c.label)}</th>" for c in cols]
        h.append("</tr>")
        for r in rows:
            bold = r.get("style") in ("total", "grand", "header")
            h.append("<tr>" + "".join(
                f"<td align='{'right' if c.type in ('money', 'qty', 'int', 'pct', 'number', 'hours') else 'left'}'>"
                f"{'<b>' if bold else ''}{_html.escape(display(c, r.get(c.key)))}{'</b>' if bold else ''}</td>"
                for c in cols) + "</tr>")
        h.append("</table></body></html>")
        printing.print_with_dialog(self, "".join(h))
