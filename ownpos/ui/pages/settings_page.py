"""Settings: store details, receipts, register and cash rules, alcohol sale hours, stations and devices,
employees and pay week, licence, backups, appearance, menu names and the AI assistant.

Store settings are saved on the server and apply to every station. Device settings (cash drawer port, pole
display) belong to THIS computer and are kept on it."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGridLayout, QLineEdit,
                               QPlainTextEdit, QScrollArea, QSpinBox, QTabWidget, QTimeEdit, QVBoxLayout, QWidget)

from ...core.backup import default_backup_folder
from ...core.hours import DAYS, describe
from .. import printing
from ..widgets.common import MoneyEdit, RefCombo, button, confirm, error, guarded, hbox, info, label, page_header
from ..widgets.grid import Col, DataGrid

PRINTERS_DEFAULT = [("", "Windows default printer")]


class SettingsPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.setObjectName("page")
        self.win = win
        self.s = win.s
        self.fields = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.addLayout(page_header("Settings", "Store settings apply to every station as soon as you press Save.",
                                  button("Save settings", "primary", self.save)))
        tabs = QTabWidget()
        tabs.addTab(self._tab([
            ("company_name", "Store name", "text"), ("company_address", "Street address", "text"),
            ("company_city_state_zip", "City, state, ZIP", "text"), ("company_phone", "Phone", "text"),
            ("company_tax_id", "Indiana Taxpayer ID / RRMC # (for your records; not printed)", "text"),
            ("company_permit_no", "ATC permit # (optional, printed on receipts)", "text"),
            ("sales_tax_frequency", "Sales tax return is filed", ("choice", [("monthly", "Monthly"),
                                                                             ("quarterly", "Quarterly"),
                                                                             ("annual", "Annually")])),
        ], note="The sales tax helper reminds you of the return due date (the 20th of the month after the period; "
                "annual returns January 31)."), "Store")
        tabs.addTab(self._tab([
            ("receipt_header", "Receipt header line", "text"),
            ("receipt_footer", "Receipt footer", "text"),
            ("receipt_width_mm", "Receipt paper width", ("choice", [(80, "80 mm (3 1/8 in)"), (58, "58 mm (2 1/4 in)")])),
            ("receipt_auto_print", "Print a receipt after every sale", "bool"),
            ("receipt_show_savings", "Show 'You saved' on receipts with discounts", "bool"),
        ], extra=[button("Preview the last receipt", slot=self.test_receipt)],
            note="Each station's receipt printer is chosen in the Stations tab."), "Receipts")
        tabs.addTab(self._tab([
            ("require_shift", "A drawer must be open to sell", "bool"),
            ("blind_count", "Blind Z-Out (the cashier counts without seeing the expected cash)", "bool"),
            ("z_count_denominations", "Z-Out counts bills and coins one by one", "bool"),
            ("max_discount_pct_cashier", "Largest discount a cashier can give (%)", "int"),
            ("cash_over_short_alert", "Warn when a drawer is over / short by more than", "money"),
            ("allow_negative_stock", "Allow selling when on hand shows zero", "bool"),
            ("slow_mover_days", "Slow mover = not sold for (days)", "int"),
            ("idle_lock_minutes", "Lock the screen after idle minutes (0 = never)", "int"),
        ]), "Register && cash")
        tabs.addTab(self._hours_tab(), "Sale hours")
        tabs.addTab(self._stations_tab(), "Stations && devices")
        tabs.addTab(self._tab([
            ("overtime_weekly_hours", "Overtime after hours in a workweek", "int"),
            ("week_starts", "Workweek starts on", ("choice", [(6, "Sunday"), (0, "Monday"), (1, "Tuesday"),
                                                             (2, "Wednesday"), (3, "Thursday"), (4, "Friday"),
                                                             (5, "Saturday")])),
        ], note="Federal law (FLSA) requires at least 1.5 times the regular rate for hours over 40 in a fixed "
                "workweek for non-exempt employees. The payroll report is an estimate of gross pay for the "
                "accountant: no tax withholding is calculated."), "Employees && pay")
        tabs.addTab(self._licence_tab(), "Licence")
        tabs.addTab(self._backup_tab(), "Backups")
        tabs.addTab(self._tab([
            ("theme", "Theme", ("choice", [("system", "Follow Windows (light or dark)"), ("light", "Light"),
                                           ("dark", "Dark")])),
        ]), "Appearance")
        tabs.addTab(self._menu_tab(), "Menu names")
        tabs.addTab(self._tab([
            ("ai_enabled", "Switch on the AI fallback of Ask OwnPOS", "bool"),
            ("ai_api_key", "Anthropic API key", "password"),
            ("ai_model", "Model (claude-haiku-5-5 recommended)", "text"),
        ], note="Ask OwnPOS answers everyday questions by itself, with no AI and no internet. Only a question it "
                "does not understand can be passed to this fallback, and only when someone presses the button. The "
                "fallback needs internet; the question and the report rows it reads are sent to Anthropic. It can "
                "only run the reports the signed-in employee may see; it cannot change anything. Claude Haiku 5.5 "
                "costs about $0.002 per question (October 2026 prices)."),
            "AI assistant")
        lay.addWidget(tabs, 1)
        self.load()

    # ---- building ------------------------------------------------------
    def _editor(self, kind):
        if kind == "bool":
            return QCheckBox()
        if kind == "int":
            ed = QSpinBox()
            ed.setRange(0, 100000)
            return ed
        if kind == "money":
            return MoneyEdit(0)
        if kind == "password":
            ed = QLineEdit()
            ed.setEchoMode(QLineEdit.Password)
            return ed
        if kind == "folder":
            ed = QLineEdit()
            ed.setPlaceholderText("Default")
            return ed
        if isinstance(kind, tuple):
            ed = QComboBox()
            for v, t in kind[1]:
                ed.addItem(t, v)
            return ed
        return QLineEdit()

    def _rows(self, form, rows):
        for key, lbl, kind in rows:
            ed = self._editor(kind)
            self.fields[key] = (kind, ed)
            if kind == "folder":
                form.addRow(lbl, hbox(ed, button("Browse…", slot=lambda _=False, e=ed: self._browse(e))))
            else:
                form.addRow(lbl, ed)

    def _scroll(self, w):
        scroll = QScrollArea()
        scroll.setWidget(w)
        scroll.setWidgetResizable(True)
        return scroll

    def _tab(self, rows, extra=None, note=""):
        w = QWidget()
        form = QFormLayout()
        self._rows(form, rows)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.addLayout(form)
        if note:
            lay.addWidget(label(note, "muted", wrap=True))
        if extra:
            lay.addLayout(hbox(*extra, None))
        lay.addStretch(1)
        return self._scroll(w)

    def _hours_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16)
        form = QFormLayout()
        self._rows(form, [("sale_hours_check", "Warn when a sale is rung up outside these hours", "bool")])
        lay.addLayout(form)
        grid = QGridLayout()
        grid.addWidget(label("Day", "sectionTitle"), 0, 0)
        grid.addWidget(label("Sales allowed", "sectionTitle"), 0, 1)
        grid.addWidget(label("From", "sectionTitle"), 0, 2)
        grid.addWidget(label("Until", "sectionTitle"), 0, 3)
        self.hours = {}
        for i, name in enumerate(DAYS):
            on = QCheckBox()
            a, b = QTimeEdit(), QTimeEdit()
            for t in (a, b):
                t.setDisplayFormat("hh:mm AP")
            grid.addWidget(label(name), i + 1, 0)
            grid.addWidget(on, i + 1, 1)
            grid.addWidget(a, i + 1, 2)
            grid.addWidget(b, i + 1, 3)
            self.hours[str(i)] = (on, a, b)
        lay.addLayout(grid)
        lay.addLayout(hbox(button("Reset to the Indiana carryout hours", slot=self._indiana_hours), None))
        self.hours_text = label("", "muted", wrap=True)
        lay.addWidget(self.hours_text)
        lay.addWidget(label(
            "An 'Until' time earlier than 'From' means after midnight (3:00 AM = 3 in the morning of the next day). "
            "Indiana carryout hours used as the default (Sunday carryout allowed since 2018): Monday to Saturday "
            "7:00 AM to 3:00 AM the next morning, Sunday 12:00 noon to 8:00 PM. This is a WARNING at the register, never a "
            "block, so a wrong PC clock can never stop the store; the sale is marked as made outside hours. "
            "Check the current law with the Indiana ATC.", "muted", wrap=True))
        lay.addStretch(1)
        return self._scroll(w)

    def _stations_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.addWidget(label("Stations (the POS computers). Each station has its own drawer. The licence decides how "
                            "many may sell.", "muted", wrap=True))
        self.st_grid = DataGrid([Col("id", "Station #", "int", width=70), Col("name", "Name", width=150),
                                 Col("computer", "Computer", width=150), Col("receipt_printer", "Receipt printer",
                                                                            width=200),
                                 Col("active", "Active", "bool", width=60), Col("last_seen", "Last seen", "datetime",
                                                                                width=150)],
                                key="stations", toolbar=False, filters=False)
        self.st_grid.show_totals = False
        self.st_grid.setMinimumHeight(180)
        lay.addWidget(self.st_grid)
        self.st_name = QLineEdit()
        self.st_printer = RefCombo(PRINTERS_DEFAULT + [(p, p) for p in printing.printer_names()])
        lay.addLayout(hbox(label("Selected station: name"), self.st_name, button("Rename", slot=self.rename_station),
                           button("Turn on / off", slot=self.toggle_station), None))
        lay.addLayout(hbox(label(f"Receipt printer of THIS station ({self.s.db.station_id})"), self.st_printer,
                           button("Save printer", slot=self.save_printer), None))
        form = QFormLayout()
        self._rows(form, [("agent_station", "Background helpers and automatic backups run on station #", "int")])
        lay.addLayout(form)
        lay.addWidget(label("Devices of THIS computer", "sectionTitle"))
        from ..branding import ui_settings
        local = ui_settings()
        self.dev_drawer_printer = RefCombo([("", "Same as the receipt printer")] +
                                           [(p, p) for p in printing.printer_names()])
        self.dev_drawer_printer.set_value(str(local.value("drawer_printer", "") or ""))
        self.dev_drawer_port = QLineEdit(str(local.value("drawer_port", "") or ""))
        self.dev_drawer_port.setPlaceholderText("e.g. COM3, only for a drawer on its own serial port")
        self.dev_display = QLineEdit(str(local.value("customer_display_port", "") or ""))
        self.dev_display.setPlaceholderText("e.g. COM4 (pole display)")
        dform = QFormLayout()
        dform.addRow("Cash drawer is plugged into printer", self.dev_drawer_printer)
        dform.addRow("…or drawer on serial port", self.dev_drawer_port)
        dform.addRow("Customer pole display port", self.dev_display)
        lay.addLayout(dform)
        lay.addLayout(hbox(button("Save devices of this computer", slot=self.save_devices),
                           button("Test drawer", slot=self.win.open_drawer), None))
        lay.addStretch(1)
        return self._scroll(w)

    def _licence_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16)
        self.lic_text = label("", wrap=True)
        lay.addWidget(self.lic_text)
        self.lic_new = QPlainTextEdit()
        self.lic_new.setPlaceholderText("To renew or change the licence, paste the new licence here (all lines, from "
                                        "-----BEGIN to END-----) and press Install.")
        self.lic_new.setMaximumHeight(140)
        lay.addWidget(self.lic_new)
        lay.addLayout(hbox(button("Load licence file…", slot=self.load_licence),
                           button("Install licence", "primary", self.install_licence),
                           button("Copy store ID", slot=self.copy_store_id), None))
        lay.addStretch(1)
        return self._scroll(w)

    def _backup_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16)
        form = QFormLayout()
        self._rows(form, [("backup_folder", "Backup folder", "folder"),
                          ("backup_extra_folder", "Second backup folder (USB / cloud-synced)", "folder"),
                          ("backup_keep", "Keep this many backups", "int"),
                          ("report_folder", "Automatic reports folder", "folder"),
                          ("pg_bin_folder", "PostgreSQL tools folder (pg_dump), if not found by itself", "folder")])
        lay.addLayout(form)
        self.backup_note = label("", "muted", wrap=True)
        lay.addWidget(self.backup_note)
        lay.addLayout(hbox(button("Back up now", slot=self.backup_now), button("Open backup folder",
                                                                                slot=self.open_backups),
                           button("Restore a backup into a NEW database…", "danger", self.restore), None))
        lay.addWidget(label(
            "Automatic backups run on the helper station (Stations tab) at each Day Closing and whenever the last "
            "backup is more than 24 hours old. Each "
            "backup is read back to prove it is complete. A restore never overwrites the live store: it creates a "
            "new database and checks that its books balance. To start using it, change each station's connection "
            "(Help > Connection, database name) to the restored database.", "muted", wrap=True))
        lay.addStretch(1)
        return self._scroll(w)

    def _menu_tab(self):
        """Rename any sidebar heading or menu entry. Empty = the standard name."""
        from ..main_window import NAV
        self.menu_edits = {}
        w = QWidget()
        form = QFormLayout()
        for gkey, heading, entries in NAV:
            ed = QLineEdit()
            ed.setPlaceholderText(f"{heading}   (type a space to hide this heading)")
            self.menu_edits["group:" + gkey] = ed
            form.addRow(label(f"Heading: {heading}", "sectionTitle"), ed)
            for key, text in entries:
                ed = QLineEdit()
                ed.setPlaceholderText(text)
                self.menu_edits[key] = ed
                form.addRow(f"      {text}".replace("&", "&&"), ed)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.addWidget(label("Type your own name next to any menu entry. Leave a box empty to keep the standard name. "
                            "Page titles inside the screens keep their standard names, so the Help guide still "
                            "matches.", "muted", wrap=True))
        lay.addLayout(form)
        lay.addStretch(1)
        return self._scroll(w)

    def _browse(self, edit):
        d = QFileDialog.getExistingDirectory(self, "Choose folder")
        if d:
            edit.setText(d)

    # ---- loading / saving ----------------------------------------------
    def load(self):
        st = self.s.db.settings()
        for key, (kind, ed) in self.fields.items():
            v = st.get(key)
            if key == "agent_station" and v is None:
                v = 1
            if kind == "bool":
                ed.setChecked(bool(v))
            elif kind == "int":
                ed.setValue(int(v or 0))
            elif kind == "money":
                ed.set_cents(int(v or 0))
            elif isinstance(kind, tuple):
                for i in range(ed.count()):
                    if ed.itemData(i) == v or str(ed.itemData(i)) == str(v):
                        ed.setCurrentIndex(i)
            else:
                ed.setText("" if v is None else str(v))
        names = st.get("menu_names") or {}
        for key, ed in self.menu_edits.items():
            ed.setText(names.get(key, ""))
        self._load_hours(st.get("sale_hours") or {})
        self._load_stations()
        self._load_licence()
        self._load_backup_note()

    def on_show(self):
        self.load()

    def _load_hours(self, hours: dict):
        from PySide6.QtCore import QTime
        for k, (on, a, b) in self.hours.items():
            spec = hours.get(k)
            enabled = bool(spec and spec[0] and spec[1])
            on.setChecked(enabled)
            for t, txt in ((a, spec[0] if enabled else "07:00"), (b, spec[1] if enabled else "03:00")):
                h, m = (int(x) for x in txt.split(":"))
                t.setTime(QTime(h, m))
        self.hours_text.setText("Now set: " + describe(hours) if hours else "No hours set.")

    def _hours_value(self) -> dict:
        out = {}
        for k, (on, a, b) in self.hours.items():
            out[k] = [a.time().toString("HH:mm"), b.time().toString("HH:mm")] if on.isChecked() else []
        return out

    def _indiana_hours(self):
        from ...core.seed import INDIANA_SALE_HOURS
        self._load_hours(INDIANA_SALE_HOURS)
        self.win.toast("Indiana hours filled in. Press Save settings to keep them.")

    @guarded
    def save(self):
        if self.win.user.role != "owner" and not self.win.user.can("settings"):
            error(self, "Your role cannot change settings.")
            return
        from ...core.auth import audit
        changed = {}
        st = self.s.db.settings()
        values = {}
        for key, (kind, ed) in self.fields.items():
            if kind == "bool":
                v = ed.isChecked()
            elif kind == "int":
                v = ed.value()
            elif kind == "money":
                v = ed.cents()
            elif isinstance(kind, tuple):
                v = ed.currentData()
            else:
                v = ed.text().strip()
            values[key] = v
        if values.get("agent_station", 1) < 1:
            error(self, "The helper station number must be 1 or more")
            return
        if not 0 <= int(values.get("max_discount_pct_cashier", 0)) <= 100:
            error(self, "A discount limit is between 0 and 100%")
            return
        values["sale_hours"] = self._hours_value()
        for key, v in values.items():
            kind = self.fields[key][0] if key in self.fields else "hours"
            if st.get(key) != v:
                changed[key] = "***" if kind == "password" else v
                self.s.db.set_setting(key, v)
        names = {k: ed.text() if ed.text().strip() == "" and ed.text() else ed.text().strip()
                 for k, ed in self.menu_edits.items() if ed.text()}
        if names != (st.get("menu_names") or {}):
            self.s.db.set_setting("menu_names", names)
            changed["menu_names"] = names
        if changed:
            audit(self.s.db, self.win.user.id, "settings_change", "settings", None, changed)
        if "theme" in changed or "menu_names" in changed:
            from PySide6.QtWidgets import QApplication
            from ..app import restart_main
            from ..theme import apply_theme
            if "theme" in changed:
                apply_theme(QApplication.instance(), changed["theme"])
            restart_main(self.win, self.win.user, page="settings")
            return
        if "register" in self.win.pages:
            self.win.pages["register"].refresh_tiles()
        self._load_hours(values["sale_hours"])
        info(self, "Settings saved." if changed else "Nothing was changed.")

    # ---- stations ------------------------------------------------------
    def _load_stations(self):
        from ...core.stations import stations
        rows = stations(self.s.db)
        self.st_grid.set_rows(rows)
        me = next((r for r in rows if r["id"] == self.s.db.station_id), None)
        self.st_printer.set_value((me or {}).get("receipt_printer") or "")

    @guarded
    def rename_station(self):
        r = self.st_grid.current_row()
        name = self.st_name.text().strip()
        if r is None or not name:
            error(self, "Click a station and type its new name")
            return
        from ...core.auth import audit
        self.s.db.exec("UPDATE stations SET name = ? WHERE id = ?", (name, r["id"]))
        audit(self.s.db, self.win.user.id, "station_rename", "station", r["id"], {"name": name})
        self.st_name.clear()
        self._load_stations()

    @guarded
    def toggle_station(self):
        r = self.st_grid.current_row()
        if r is None:
            error(self, "Click a station first")
            return
        from ...core.stations import set_active
        if r["active"] and not confirm(self, f"Turn off {r['name']}? It can then not sell until it is turned on "
                                             "again (use this for a retired PC, to free its licence place)."):
            return
        set_active(self.s.db, r["id"], not r["active"], self.win.user.id)
        self._load_stations()

    @guarded
    def save_printer(self):
        self.s.db.exec("UPDATE stations SET receipt_printer = ? WHERE id = ?",
                       (self.st_printer.value() or None, self.s.db.station_id))
        self._load_stations()
        self.win.toast("Receipt printer saved for this station.")

    def save_devices(self):
        from ..branding import ui_settings
        local = ui_settings()
        local.setValue("drawer_printer", self.dev_drawer_printer.value() or "")
        local.setValue("drawer_port", self.dev_drawer_port.text().strip())
        local.setValue("customer_display_port", self.dev_display.text().strip())
        self.win.toast("Devices saved on this computer.")

    # ---- licence ---------------------------------------------------------
    def _load_licence(self):
        from ...core.licence import allowed_stations, status, store_uid
        st = status(self.s.db)
        p = st.payload or {}
        lines = [f"Store ID: {store_uid(self.s.db)}", f"State: {st.state}" + (f" - {st.message}" if st.message else "")]
        if p:
            lines += [f"Licensed to: {p.get('licensee', '')}", f"Stations that may sell: {p.get('stations', '')}",
                      f"Ends: {p.get('expires') or 'does not end'}", f"Issued: {p.get('issued', '')}"]
        lim = allowed_stations(self.s.db)
        if lim is not None:
            lines.append(f"Station limit in force: {lim}")
        self.lic_text.setText("\n".join(lines))

    def load_licence(self):
        path, _ = QFileDialog.getOpenFileName(self, "Licence file", "", "OwnPOS licence (*.ownpos-licence);;All files (*)")
        if path:
            with open(path, encoding="utf-8") as fh:
                self.lic_new.setPlainText(fh.read())

    @guarded
    def install_licence(self):
        if self.win.user.role != "owner":
            error(self, "Only the owner can install a licence.")
            return
        from ...core.licence import LicenceError, install
        try:
            payload = install(self.s.db, self.lic_new.toPlainText(), self.win.user.id)
        except LicenceError as e:
            error(self, str(e))
            return
        self.lic_new.clear()
        self._load_licence()
        info(self, f"Licence installed for {payload.get('licensee')}: {payload.get('stations')} station(s). "
                   "Restart OwnPOS on each station to apply it.")

    def copy_store_id(self):
        from PySide6.QtWidgets import QApplication
        from ...core.licence import store_uid
        QApplication.clipboard().setText(store_uid(self.s.db))
        self.win.toast("Store ID copied.")

    # ---- backups ---------------------------------------------------------
    def _load_backup_note(self):
        db = self.s.db
        folder = default_backup_folder(db)
        from ...core.backup import SUFFIX
        files = sorted(folder.glob(f"*{SUFFIX}")) if folder.exists() else []
        last = db.get_setting("last_backup_at") or "never"
        chosen = str(db.get_setting("backup_folder") or "").strip()
        lost = ""
        if chosen and Path(folder) != Path(chosen):
            lost = (f"\nThe chosen backup folder {chosen} cannot be reached from this computer, so backups made here "
                    "go to the folder above.")
        self.backup_note.setText(f"Backups made on this computer go to: {folder}\n{len(files)} backup(s) there; last "
                                 f"backup (any station): {last}.{lost}")

    def open_backups(self):
        import os
        import subprocess
        import sys
        folder = default_backup_folder(self.s.db)
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            os.startfile(str(folder))  # noqa: S606 - opens Explorer on the backup folder
        else:
            subprocess.Popen(["xdg-open", str(folder)])  # noqa: S603,S607

    @guarded
    def backup_now(self):
        if not self.win.user.can("backup"):
            error(self, "Your role cannot make backups.")
            return
        from ...core.backup import backup_all
        paths = backup_all(self.s.db, self.s.conninfo, "manual")
        self._load_backup_note()
        info(self, "Backup saved and checked:\n" + "\n".join(str(p) for p in paths))

    @guarded
    def restore(self):
        if self.win.user.role != "owner":
            error(self, "Only the owner can restore a backup.")
            return
        from ...core.backup import SUFFIX, restore_to_new_database
        path, _ = QFileDialog.getOpenFileName(self, "Choose a backup", str(default_backup_folder(self.s.db)),
                                              f"OwnPOS backup (*{SUFFIX})")
        if not path:
            return
        if not confirm(self, "Restore this backup into a NEW database on the server? The live store database is "
                             "not changed. You then switch the stations to the new database yourself."):
            return
        name = restore_to_new_database(self.s.db, self.s.conninfo, Path(path))
        from ...core.auth import audit
        audit(self.s.db, self.win.user.id, "restore_new_db", "backup", None, {"file": path, "database": name})
        info(self, f"Restored into the new database '{name}' and checked (books balance).\n\nTo use it, open the "
                   "connection screen on each station (Help > Connection) and change the database name to "
                   f"'{name}'.")

    @guarded
    def test_receipt(self):
        sid = self.s.db.val("SELECT id FROM sales ORDER BY id DESC LIMIT 1")
        if not sid:
            info(self, "Make one sale first, then preview the receipt.")
            return
        from ...core.receipt import receipt_html
        html = receipt_html(self.s.db, sid, copy_no=1)
        printing.preview_html(self, html, int(self.s.db.get_setting("receipt_width_mm", 80)), "Receipt preview")
