"""First start on a computer: make this PC the store server, or connect it to the store server as a station.
Also: the licence (activation) screen."""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFileDialog, QFormLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .. import APP_NAME, APP_VERSION
from ..core import connection as conn_mod
from ..core.db import ServerUnavailable
from .branding import logo_pixmap
from .widgets.common import button, confirm, error, hbox, info, label, vbox


def _logo_header(title: str, sub: str) -> QVBoxLayout:
    logo = QLabel()
    logo.setPixmap(logo_pixmap(56))
    return vbox(hbox(logo, vbox(label(title, "pageTitle"), label(sub, "muted", wrap=True), spacing=2), None), spacing=6)


class ConnectionDialog(QDialog):
    """Where is the store server? Two tabs: connect this station, or set this PC up as the server."""

    def __init__(self, current: conn_mod.Connection | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} - store server")
        self.setMinimumWidth(620)
        self.result_conn: conn_mod.Connection | None = None
        c = current or conn_mod.Connection()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.addLayout(_logo_header(f"{APP_NAME} {APP_VERSION}",
                                   "All stations share one store database on the server PC. Choose what this computer is."))
        self.tabs = QTabWidget()
        self.tabs.addTab(self._station_tab(c), "Connect this station to the store server")
        self.tabs.addTab(self._server_tab(), "Set up THIS PC as the store server")
        lay.addWidget(self.tabs)

    # ---- station ------------------------------------------------------------------------------
    def _station_tab(self, c) -> QWidget:
        w = QWidget()
        self.host = QLineEdit(c.host)
        self.host.setPlaceholderText("Server PC name or address, e.g. STORE-SERVER or 192.168.1.20")
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(int(c.port))
        self.dbname = QLineEdit(c.dbname)
        self.user = QLineEdit(c.user)
        self.password = QLineEdit(c.password)
        self.password.setEchoMode(QLineEdit.Password)
        self.station = QSpinBox()
        self.station.setRange(1, 99)
        self.station.setValue(int(c.station_id or 1))
        self.station_name = QLineEdit(c.station_name or "")
        self.station_name.setPlaceholderText("e.g. Front counter")
        form = QFormLayout()
        form.addRow("Server", self.host)
        form.addRow("Port", self.port)
        form.addRow("Store database", self.dbname)
        form.addRow("Login", self.user)
        form.addRow("Password", self.password)
        form.addRow("This station's number", self.station)
        form.addRow("This station's name", self.station_name)
        self.msg = label("", "muted", wrap=True)
        w.setLayout(vbox(
            label("Each station needs a different number. The number decides which cash drawer it uses.", "muted", wrap=True),
            form, self.msg,
            hbox(button("Load station setup file…", slot=self.load_file), None,
                 button("Test connection", slot=self.test), button("Save and continue", "primary", self.save)),
            margins=(12, 12, 12, 12)))
        return w

    def _conn(self) -> conn_mod.Connection:
        return conn_mod.Connection(host=self.host.text().strip() or "localhost", port=self.port.value(),
                                   dbname=self.dbname.text().strip(), user=self.user.text().strip(),
                                   password=self.password.text(), station_id=self.station.value(),
                                   station_name=self.station_name.text().strip())

    def load_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Station setup file", "", "OwnPOS station file (*.json);;All files (*)")
        if not path:
            return
        from ..core.serversetup import SetupError, read_station_file
        try:
            data = read_station_file(path)
        except (SetupError, ValueError, OSError) as e:
            error(self, str(e))
            return
        self.host.setText(str(data["host"]))
        self.port.setValue(int(data["port"]))
        self.dbname.setText(data["dbname"])
        self.user.setText(data["user"])
        self.password.setText(data["password"])
        self.msg.setText("Loaded. Choose this station's number, then Test connection.")

    def test(self) -> bool:
        from ..core.db import Database
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            db = Database(self._conn().conninfo(), read_only=True)
            version = db.val("SHOW server_version")
            used = [r[0] for r in db.all("SELECT id FROM stations ORDER BY id")] if db.val(
                "SELECT 1 FROM information_schema.tables WHERE table_name = 'stations'") else []
            db.close()
        except ServerUnavailable as e:
            QApplication.restoreOverrideCursor()
            self.msg.setText(f"Not connected: {e}")
            return False
        QApplication.restoreOverrideCursor()
        note = f" Stations already set up: {', '.join(map(str, used))}." if used else ""
        self.msg.setText(f"Connected to PostgreSQL {version}.{note}")
        return True

    def save(self):
        if not self.dbname.text().strip() or not self.user.text().strip():
            error(self, "Fill in the store database and login")
            return
        if not self.test():
            if not confirm(self, "The server could not be reached. Save these settings anyway?"):
                return
        c = self._conn()
        try:
            conn_mod.save(c)
        except OSError as e:
            error(self, f"Could not save the connection: {e}")
            return
        self.result_conn = c
        self.accept()

    # ---- server -------------------------------------------------------------------------------
    def _server_tab(self) -> QWidget:
        w = QWidget()
        self.admin_pw = QLineEdit()
        self.admin_pw.setEchoMode(QLineEdit.Password)
        self.admin_pw.setPlaceholderText("The password chosen when PostgreSQL was installed (user 'postgres')")
        self.srv_port = QSpinBox()
        self.srv_port.setRange(1, 65535)
        self.srv_port.setValue(5432)
        self.srv_db = QLineEdit("ownpos_store")
        self.make_demo = QCheckBox("Also make a sample store database for training (ownpos_demo)")
        self.make_demo.setChecked(True)
        form = QFormLayout()
        form.addRow("PostgreSQL administrator password", self.admin_pw)
        form.addRow("Port", self.srv_port)
        form.addRow("Store database name", self.srv_db)
        form.addRow("", self.make_demo)
        self.srv_msg = QPlainTextEdit()
        self.srv_msg.setReadOnly(True)
        self.srv_msg.setMaximumHeight(130)
        self._server_info = None
        steps = label(
            "Before this: install PostgreSQL on this PC (see the setup guide). Then:\n"
            "1. Create the store database (below).\n"
            "2. Allow the stations to connect (Windows asks for administrator permission).\n"
            "3. Save a station setup file to a USB stick for the other PCs.\n"
            "OwnPOS does not keep the administrator password.", "muted", wrap=True)
        self.btn_create = button("1. Create the store database", "primary", self.create_db)
        self.btn_net = button("2. Allow stations to connect", slot=self.allow_net)
        self.btn_file = button("3. Save station setup file…", slot=self.save_file)
        self.btn_use = button("Use this PC as station 1 and continue", "primary", self.use_here)
        for b in (self.btn_net, self.btn_file, self.btn_use):
            b.setEnabled(False)
        w.setLayout(vbox(steps, form, hbox(self.btn_create, self.btn_net, self.btn_file, None), self.srv_msg,
                         hbox(None, self.btn_use), margins=(12, 12, 12, 12)))
        return w

    def _log(self, text: str):
        self.srv_msg.appendPlainText(text)

    def create_db(self):
        from ..core.serversetup import SetupError, create_store
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._server_info = create_store("localhost", self.srv_port.value(), "postgres", self.admin_pw.text(),
                                             self.srv_db.text().strip() or "ownpos_store",
                                             demo=self.make_demo.isChecked())
        except SetupError as e:
            QApplication.restoreOverrideCursor()
            error(self, str(e))
            return
        QApplication.restoreOverrideCursor()
        i = self._server_info
        self._log(f"PostgreSQL {i['server_version']}: login 'ownpos' ready; databases created: "
                  f"{', '.join(i['created']) or 'none (already there)'}.")
        for b in (self.btn_net, self.btn_file, self.btn_use):
            b.setEnabled(True)

    def allow_net(self):
        """Run the network step with administrator rights (Windows shows its permission prompt)."""
        i = self._server_info
        if not i:
            return
        dbs = [i["dbname"]] + ([i["demo_db"]] if i.get("demo_db") else [])
        restart = i["listen_addresses"].strip() not in ("*", "0.0.0.0")
        if restart:
            from ..core.serversetup import _admin
            with _admin("localhost", self.srv_port.value(), "postgres", self.admin_pw.text()) as c:
                c.execute("ALTER SYSTEM SET listen_addresses = '*'")
            self._log("PostgreSQL will listen on the network (restart needed; done in the next step).")
        if sys.platform != "win32":
            self._log("This step is for Windows. On this system add to pg_hba.conf:\n" +
                      __import__("ownpos.core.serversetup", fromlist=["hba_line"]).hba_line(dbs))
            return
        import ctypes
        exe = sys.executable
        args = (["-m", "ownpos"] if not getattr(sys, "frozen", False) else []) + [
            "--allow-network", i["hba_file"], str(self.srv_port.value()), ",".join(dbs), "1" if restart else "0"]
        params = " ".join(f'"{a}"' for a in args)
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 0)
        if rc <= 32:
            error(self, "Windows did not run the step (permission was refused).")
            return
        info(self, "When the administrator step has finished (a few seconds), press OK to reload PostgreSQL.")
        try:
            from ..core.serversetup import _admin
            with _admin("localhost", self.srv_port.value(), "postgres", self.admin_pw.text()) as c:
                c.execute("SELECT pg_reload_conf()")
            self._log("Stations on this shop network may now connect (Private network, password required).")
        except Exception as e:  # noqa: BLE001
            self._log(f"Reload: {e}")

    def save_file(self):
        if not self._server_info:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Station setup file", "ownpos-station-setup.json", "JSON (*.json)")
        if not path:
            return
        import socket
        info_ = dict(self._server_info, host=socket.gethostname())
        from ..core.serversetup import station_file
        station_file(path, info_)
        self._log(f"Saved {path}. It contains the database password: delete it from the stick after use.")

    def use_here(self):
        i = self._server_info
        c = conn_mod.Connection(host="localhost", port=self.srv_port.value(), dbname=i["dbname"], user=i["user"],
                                password=i["password"], station_id=1, station_name="Server")
        conn_mod.save(c)
        self.result_conn = c
        self.accept()


class ActivationDialog(QDialog):
    """No valid licence yet: show the store ID, paste or load the licence. Can open read-only instead."""

    def __init__(self, s, status, parent=None):
        super().__init__(parent)
        from ..core.licence import store_uid
        self.s = s
        self.read_only = False
        self.setWindowTitle(f"{APP_NAME} - activation")
        self.setMinimumWidth(620)
        uid = store_uid(s.db)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Paste the licence here (all lines, from -----BEGIN to END-----)")
        uid_box = QLineEdit(uid)
        uid_box.setReadOnly(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.addLayout(_logo_header("Activate OwnPOS", status.message))
        lay.addWidget(label("Send this store ID to Aayush to receive the licence:", "sectionTitle"))
        lay.addLayout(hbox(uid_box, button("Copy", slot=lambda: QApplication.clipboard().setText(uid))))
        lay.addWidget(self.text)
        lay.addLayout(hbox(button("Load licence file…", slot=self.load), None,
                           button("Open without selling (reports, backups)", slot=self.open_ro),
                           button("Activate", "primary", self.activate)))

    def load(self):
        path, _ = QFileDialog.getOpenFileName(self, "Licence file", "", "OwnPOS licence (*.ownpos-licence);;All files (*)")
        if path:
            with open(path, encoding="utf-8") as fh:
                self.text.setPlainText(fh.read())

    def activate(self):
        from ..core.licence import LicenceError, install
        try:
            payload = install(self.s.db, self.text.toPlainText(), None)
        except LicenceError as e:
            error(self, str(e))
            return
        info(self, f"Activated for {payload.get('licensee')}: {payload.get('stations')} station(s)"
                   + (f", until {payload.get('expires')}." if payload.get("expires") else "."))
        self.accept()

    def open_ro(self):
        self.read_only = True
        self.accept()
