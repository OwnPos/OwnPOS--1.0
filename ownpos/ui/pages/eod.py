"""End of Day: this station's drawer (X-Out so far, payouts and drops, Z-Out with a bill-and-coin count) and the
store's Day Closing report (all stations together, with the day's sales tax)."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QFormLayout, QGridLayout, QHBoxLayout, QSpinBox, QTextBrowser, QVBoxLayout,
                               QWidget)

from ...core.db import today_str
from ...core.money import fmt_money
from ...core.receipt import day_closing_html, zout_html
from .. import printing
from ..dialogs import BaseDialog, CashMoveDialog
from ..widgets.common import (MoneyEdit, button, confirm, error, guarded, hbox, info, label, page_header, panel,
                              require, rs, vbox)
from ..widgets.grid import Col, DataGrid


class CountDialog(BaseDialog):
    def __init__(self, parent, denominations, expected: int, require_count: bool, blind: bool = True):
        super().__init__(parent, "Z-Out: count the drawer", 460)
        self.denoms = denominations
        self.spins = {}
        grid = QGridLayout()
        grid.addWidget(label("Note / coin", "muted"), 0, 0)
        grid.addWidget(label("How many", "muted"), 0, 1)
        grid.addWidget(label("Amount", "muted"), 0, 2)
        self.amts = {}
        for i, d in enumerate(denominations, start=1):
            sp = QSpinBox()
            sp.setRange(0, 100000)
            sp.valueChanged.connect(self._total)
            self.spins[d] = sp
            a = label("0.00")
            a.setAlignment(Qt.AlignRight)
            self.amts[d] = a
            grid.addWidget(label(f"${d // 100}" if d >= 100 else f"{d}¢"), i, 0)
            grid.addWidget(sp, i, 1)
            grid.addWidget(a, i, 2)
        self.lay.addLayout(grid)
        self.total_lbl = label("", "sectionTitle")
        self.lay.addWidget(self.total_lbl)
        self.direct = MoneyEdit(0)
        self.direct.setText("")
        if not require_count:
            form = QFormLayout()
            form.addRow("…or type the counted total", self.direct)
            self.lay.addLayout(form)
            self.direct.textChanged.connect(self._total)
        if blind:
            self.lay.addWidget(label("Blind count: the expected amount is shown after you finish, so the count "
                                     "stays honest.", "muted"))
        else:
            self.lay.addWidget(label(f"Expected in the drawer: {rs(expected)}", "sectionTitle"))
        self.buttons("Close drawer and print Z-Out")
        self._total()

    def _total(self, *_):
        t = 0
        for d, sp in self.spins.items():
            v = d * sp.value()
            self.amts[d].setText(fmt_money(v))
            t += v
        if self.direct.text():
            t = self.direct.paisa()
        self.total = t
        self.total_lbl.setText(f"Counted: {rs(t)}")

    def counts(self):
        return {str(d): sp.value() for d, sp in self.spins.items() if sp.value()}


class EndOfDayPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.setObjectName("page")
        self.win = win
        self.s = win.s
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        self.open_btn = button("Open drawer", "blue", self.open_shift)
        lay.addLayout(page_header("End of Day", f"{win.station_name}: X-Out shows this drawer so far; Z-Out counts and "
                                  "closes it. After every station's Z-Out, Day Closing adds up the whole store (sales "
                                  "tax included), writes the day's reports and summary, and backs up.",
                                  self.open_btn, button("Pay out", slot=lambda: self.cash("payout")),
                                  button("Cash drop", slot=lambda: self.cash("drop")),
                                  button("Print X-Out", slot=self.print_x), button("Z-Out (close drawer)", "primary", self.z_out),
                                  button("Day Closing (whole store)", slot=self.day_closing)))
        body = QHBoxLayout()
        p = panel()
        pl = QVBoxLayout(p)
        pl.setContentsMargins(14, 12, 14, 12)
        pl.addWidget(label("This drawer so far (X-Out)", "sectionTitle"))
        self.x_view = QTextBrowser()
        self.x_view.setStyleSheet("border:none")
        pl.addWidget(self.x_view, 1)
        body.addWidget(p, 2)
        right = QVBoxLayout()
        right.addWidget(label("Drawer history (all stations)", "sectionTitle"))
        self.hist = DataGrid([Col("z_number", "Z #", width=70), Col("station", "Station", width=90),
                              Col("opened_at", "Opened", "datetime", width=140),
                              Col("closed_at", "Closed", "datetime", width=140), Col("cashier", "Cashier", width=120),
                              Col("expected_cash", "Expected", "money"), Col("counted_cash", "Counted", "money"),
                              Col("over_short", "Over/short", "money")], key="shifts", export_title="Shifts")
        self.hist.activated.connect(self.reprint_z)
        right.addWidget(self.hist, 1)
        body.addLayout(right, 3)
        lay.addLayout(body, 1)

    def on_show(self):
        sh = self.s.shifts.current()
        self.open_btn.setEnabled(sh is None)
        if sh:
            self.x_view.setHtml(zout_html(self.s.db, self.s.shifts.summary(sh["id"]), "X-OUT (shift so far)"))
        else:
            self.x_view.setHtml("<p>This station's drawer is not open. Open it to start selling.</p>")
        rows = self.s.db.dicts("SELECT s.*, u.full_name AS cashier, COALESCE(st.name, 'Station ' || s.station_id) AS station"
                               " FROM shifts s JOIN users u ON u.id = s.user_id LEFT JOIN stations st ON st.id = s.station_id"
                               " WHERE s.status = 'closed' ORDER BY s.id DESC LIMIT 300")
        for r in rows:
            if r["over_short"]:
                r["_bg"] = "#FEF3F2" if r["over_short"] < 0 else "#ECFDF3"
        self.hist.set_rows(rows)
        self.win.refresh_status()

    @guarded
    def open_shift(self):
        self.win.ensure_shift()
        self.on_show()

    @guarded
    def cash(self, kind):
        approver = require(self.win, "payout", "Moving cash")
        if approver is None:
            return
        d = CashMoveDialog(self, self.s, kind)
        if d.exec() == QDialog.Accepted:
            self.s.shifts.cash_move(kind, d.amount.paisa(), approver.id, d.reason.currentText(), d.reason.currentData(),
                                    d.note.text())
            self.win.open_drawer()
            self.on_show()

    @guarded
    def print_x(self):
        sh = self.s.shifts.current()
        if not sh:
            error(self, "This station's drawer is not open")
            return
        html = zout_html(self.s.db, self.s.shifts.summary(sh["id"]), "X-OUT")
        self._print(html, "X-Out")

    def _print(self, html: str, title: str):
        printer = self.s.db.val("SELECT receipt_printer FROM stations WHERE id = ?", (self.s.db.station_id,)) or ""
        width = int(self.s.db.get_setting("receipt_width_mm", 80) or 80)
        if not printing.print_html(html, printer, width):
            printing.preview_html(self, html, width, title)

    @guarded
    def z_out(self):
        sh = self.s.shifts.current()
        if not sh:
            error(self, "This station's drawer is not open")
            return
        if require(self.win, "shift", "Closing the drawer (Z-Out)") is None:
            return
        mine = [h for h in self.s.sales.holds() if h["station_id"] == self.s.db.station_id]
        if mine and not confirm(self, "This station has held sales. Close the drawer anyway?"):
            return
        st = self.s.db.settings()
        summ = self.s.shifts.summary(sh["id"])
        d = CountDialog(self, st.get("denominations", [10000, 5000, 2000, 1000, 500, 200, 100, 25, 10, 5, 1]),
                        summ["expected_cash"], bool(st.get("z_count_denominations", True)),
                        blind=bool(st.get("blind_count", True)))
        if d.exec() != QDialog.Accepted:
            return
        res = self.s.shifts.close(self.win.user.id, d.total, d.counts())
        self._print(zout_html(self.s.db, res, "Z-OUT"), "Z-Out")
        os_ = res["over_short"]
        alert = int(st.get("cash_over_short_alert", 500) or 0)
        warn = (f"ATTENTION: the drawer is off by more than {rs(alert)}. Recount before leaving, and check payouts "
                "and drops on the Z-Out.\n\n") if alert and abs(os_) > alert else ""
        msg = warn + (f"{res['z_number']} closed. Expected {rs(res['expected_cash'])}, counted {rs(res['counted_cash'])}: "
               + ("drawer balanced." if os_ == 0 else f"{'over' if os_ > 0 else 'short'} by {rs(abs(os_))}."))
        still = [x for x in self.s.shifts.open_shifts()]
        if still:
            msg += "\n\nStill open: " + ", ".join(f"{x['station_name'] or 'Station ' + str(x['station_id'])} "
                                                  f"({x['full_name']})" for x in still)
        else:
            msg += "\n\nEvery drawer is closed. Run Day Closing to finish the day."
        info(self, msg, "Z-Out complete")
        self.on_show()

    @guarded
    def day_closing(self):
        """The whole store for today: all stations, sales tax, payments, drawers. Runs the day-close agents
        (reports workbook, summary, reorder, backup) once."""
        if require(self.win, "x_report", "Day Closing") is None:
            return
        day = today_str()
        dc = self.s.shifts.day_closing(day)
        if dc["open_drawers"] and not confirm(
                self, f"{len(dc['open_drawers'])} drawer(s) are still open. Print the Day Closing anyway? "
                      "(Their sales so far are included.)"):
            return
        self._print(day_closing_html(self.s.db, dc), "Day Closing")
        if not dc["open_drawers"]:
            self.win.toast("Day Closing: writing reports, summary and backup…", 4000)
            out = self.s.agents.run_schedule("day_close", force=True)
            s = self.s.db.one("SELECT text FROM summaries ORDER BY id DESC LIMIT 1")
            msg = (s["text"] if s else "Day closed.") + (f"\n\n{out['reports']}" if "reports" in out else "")
            if "backup" in out:
                msg += f"\n{out['backup']}"
            info(self, msg, "Day Closing complete")

    def reprint_z(self, row):
        summ = self.s.shifts.summary(row["id"])
        summ["counted_cash"] = row["counted_cash"]
        summ["over_short"] = row["over_short"]
        summ["z_number"] = row["z_number"]
        printing.preview_html(self, zout_html(self.s.db, summ, "Z-OUT (copy)"), 80, row["z_number"])
