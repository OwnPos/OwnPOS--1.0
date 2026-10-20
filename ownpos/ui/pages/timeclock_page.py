"""Employees > Time Clock: who is in, every punch, weekly hours with overtime, and fixing a missed punch.

Everyone sees their own punches. With "Fix time clock punches" a manager sees everyone and can add or correct a
punch (a reason is required and logged). With "See pay rates and the payroll report" the weekly table also shows
an estimate of gross pay for the accountant (no withholding)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import QDateTime
from PySide6.QtWidgets import QCheckBox, QComboBox, QDateTimeEdit, QFormLayout, QLineEdit, QVBoxLayout, QWidget

from ...core.db import now_dt, today_str
from ..dialogs import BaseDialog
from ..widgets.common import DateEdit, RefCombo, button, error, guarded, hbox, info, label, page_header
from ..widgets.grid import Col, DataGrid, us_datetime


class PunchDialog(BaseDialog):
    """Add a missed punch or correct one."""
    def __init__(self, parent, s, row: dict | None, actor):
        super().__init__(parent, "Fix a punch" if row else "Add a missed punch", 460)
        self.s = s
        self.actor = actor
        self.row = row
        self.user = RefCombo([(r["id"], r["full_name"]) for r in s.db.all(
            "SELECT id, full_name FROM users WHERE active = 1 OR id = ? ORDER BY full_name",
            ((row or {}).get("user_id") or 0,))])
        now = now_dt()
        self.t_in = QDateTimeEdit()
        self.t_out = QDateTimeEdit()
        for w in (self.t_in, self.t_out):
            w.setCalendarPopup(True)
            w.setDisplayFormat("MM/dd/yyyy hh:mm AP")
        self.still_in = QCheckBox("Still clocked in (no clock-out yet)")
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("Required, e.g. forgot to clock out")
        if row:
            self.user.set_value(row["user_id"])
            self.user.setEnabled(False)
            self._set(self.t_in, row["clock_in"])
            if row["clock_out"]:
                self._set(self.t_out, row["clock_out"])
            else:
                self.t_out.setDateTime(QDateTime(now))
                self.still_in.setChecked(True)
        else:
            self.t_in.setDateTime(QDateTime(now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=8)))
            self.t_out.setDateTime(QDateTime(now.replace(second=0, microsecond=0)))
        self.still_in.toggled.connect(lambda on: self.t_out.setEnabled(not on))
        self.t_out.setEnabled(not self.still_in.isChecked())
        form = QFormLayout()
        form.addRow("Employee", self.user)
        form.addRow("Clocked in", self.t_in)
        form.addRow("Clocked out", self.t_out)
        form.addRow("", self.still_in)
        form.addRow("Reason *", self.reason)
        self.lay.addLayout(form)
        self.hours = label("", "muted")
        self.lay.addWidget(self.hours)
        for w in (self.t_in, self.t_out):
            w.dateTimeChanged.connect(self._hours)
        self.still_in.toggled.connect(self._hours)
        self._hours()
        self.buttons("Save punch")

    @staticmethod
    def _set(w, text):
        w.setDateTime(QDateTime(datetime.strptime(text[:16], "%Y-%m-%d %H:%M")))

    @staticmethod
    def _text(w) -> str:
        return w.dateTime().toPython().strftime("%Y-%m-%d %H:%M")

    def _hours(self, *_):
        if self.still_in.isChecked():
            self.hours.setText("Open punch")
            return
        h = (self.t_out.dateTime().toPython() - self.t_in.dateTime().toPython()).total_seconds() / 3600
        self.hours.setText(f"{h:.2f} hours")

    def validate(self):
        if not self.reason.text().strip():
            error(self, "Give the reason for the change (it is kept in the log)")
            return False
        try:
            self.s.timeclock.save_entry(self.user.value(), self._text(self.t_in),
                                        None if self.still_in.isChecked() else self._text(self.t_out),
                                        self.reason.text().strip(), self.actor,
                                        self.row["id"] if self.row else None)
        except Exception as exc:  # noqa: BLE001 - overlap, future time, too long...
            error(self, str(exc))
            return False
        return True


class TimeClockPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.setObjectName("page")
        self.win = win
        self.s = win.s
        u = win.user
        self.can_edit = u.can("timeclock_edit")
        self.can_pay = u.can("payroll")
        self.sees_all = self.can_edit or self.can_pay or u.can("employees")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        acts = [button("Clock me in / out", "primary", self.punch_me)]
        if self.can_edit:
            acts += [button("Add missed punch", slot=self.add_punch), button("Fix selected punch", slot=self.fix_punch)]
        lay.addLayout(page_header("Time Clock", "Employees clock in and out on the sign-in screen (PIN, then Clock in "
                                  "/ Clock out) or here.", *acts))
        self.who = label("", "sectionTitle", wrap=True)
        lay.addWidget(self.who)
        self.preset = QComboBox()
        self.preset.addItems(["This week", "Last week", "This month", "Last month", "Custom"])
        self.preset.currentTextChanged.connect(self._preset)
        self.d_from, self.d_to = DateEdit(), DateEdit()
        self.emp = RefCombo([], blank="All employees")
        self.emp.setVisible(self.sees_all)
        lay.addLayout(hbox(self.preset, label("From"), self.d_from, label("To"), self.d_to, self.emp,
                           button("Show", slot=self.reload), None))
        self.grid = DataGrid([Col("full_name", "Employee", width=170), Col("clock_in", "Clocked in", "datetime",
                                                                          width=160),
                              Col("clock_out", "Clocked out", "datetime", width=160), Col("hours", "Hours", "hours"),
                              Col("station_id", "Station", "int", width=60), Col("edited", "Changed", width=70),
                              Col("edit_reason", "Reason for change", width=200)],
                             key="timeclock", export_title="Time clock punches")
        self.grid.activated.connect(lambda _r: self.fix_punch() if self.can_edit else None)
        lay.addWidget(label("Punches", "sectionTitle"))
        lay.addWidget(self.grid, 2)
        week_cols = [Col("employee", "Employee", width=170), Col("week_start", "Week from", "date", width=100),
                     Col("week_end", "to", "date", width=100), Col("hours", "Hours", "hours"),
                     Col("regular", "Regular", "hours"), Col("overtime", "Overtime", "hours"),
                     Col("open_punches", "Open punches", "int", width=90), Col("edited", "Changed", "int", width=70)]
        if self.can_pay:
            week_cols += [Col("rate", "Rate", "money", total=False), Col("gross_pay", "Estimated gross pay", "money")]
        self.weeks = DataGrid(week_cols, key="timeclock_weeks", export_title="Hours by workweek")
        lay.addWidget(label("Hours by workweek (overtime over the weekly limit in Settings > Employees & pay; whole "
                            "workweeks are shown)", "sectionTitle"))
        lay.addWidget(self.weeks, 1)
        self._preset("This week")

    def _preset(self, name):
        if name == "Custom":
            return
        from .reports_page import preset_range
        ws = self.s.db.get_setting("week_starts", 6)
        a, b = preset_range(name, date.fromisoformat(today_str()), 6 if ws in (None, "") else int(ws))
        self.d_from.set_date(a)
        self.d_to.set_date(b)
        self.reload()

    def on_show(self):
        emp = self.emp.value()
        self.emp.set_choices([(r["id"], r["full_name"]) for r in self.s.db.all(
            "SELECT id, full_name FROM users ORDER BY full_name")], blank="All employees")
        self.emp.set_value(emp)
        self.reload()

    def reload(self):
        rows = self.s.timeclock.who_is_in()
        self.who.setText("Clocked in now: " + ", ".join(f"{r['full_name']} (since {us_datetime(r['clock_in'])})"
                                                       for r in rows) if rows else "Nobody is clocked in.")
        user_id = self.emp.value() if self.sees_all else self.win.user.id
        a, b = self.d_from.iso(), self.d_to.iso()
        if a > b:
            return
        entries = self.s.timeclock.entries(a, b, user_id)
        for e in entries:
            e["edited"] = "Yes" if e["edited_by"] else ""
            if not e["clock_out"]:
                e["_bg"] = "#FFFAEB"
        self.grid.set_rows(entries)
        weeks = self.s.timeclock.payroll(a, b, with_pay=self.can_pay)
        if user_id:
            name = self.s.db.val("SELECT full_name FROM users WHERE id = ?", (user_id,))
            weeks = [w for w in weeks if w["employee"] == name]
        self.weeks.set_rows(weeks)

    @guarded
    def punch_me(self):
        from ...core.timeclock import TimeClockError
        u = self.win.user
        try:
            if self.s.timeclock.open_entry(u.id):
                r = self.s.timeclock.clock_out(u.id)
                info(self, f"{u.full_name}: clocked out after {r['hours']:.2f} hours.")
            else:
                self.s.timeclock.clock_in(u.id)
                info(self, f"{u.full_name}: clocked in.")
        except TimeClockError as e:
            error(self, str(e))
        self.reload()

    @guarded
    def add_punch(self):
        if PunchDialog(self, self.s, None, self.win.user).exec():
            self.reload()
            self.win.toast("Punch added.")

    @guarded
    def fix_punch(self):
        r = self.grid.current_row()
        if r is None:
            error(self, "Click the punch to fix first")
            return
        if PunchDialog(self, self.s, r, self.win.user).exec():
            self.reload()
            self.win.toast("Punch saved.")
