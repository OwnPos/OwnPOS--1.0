"""Program start: find the store server, check the licence, sign in, open the main window.

Start-up, in order:
1. This computer's connection file (server, database, station number). None yet -> the connection screen
   (connect as a station, or set this PC up as the server).
2. Connect to the store database (layout updated if this is a newer OwnPOS). Server not reachable -> a clear
   message with Retry / change settings.
3. Register this station; the licence says whether it may sell.
4. Licence check (outer layer; never touches the store's records).
5. First run (no employees yet) -> store details and the owner login.
6. Sign in (PIN or password; the same screen clocks people in and out).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from .. import APP_NAME
from ..core import connection as conn_mod
from ..core.db import ServerUnavailable
from ..core.services import Services
from .branding import ORG_NAME, app_icon, set_windows_app_id
from .theme import apply_theme, current_mode

_windows = []
_open = {"services": None}


def make_app(argv=None) -> QApplication:
    set_windows_app_id()
    app = QApplication.instance() or QApplication(argv or sys.argv)
    app.setWindowIcon(app_icon())
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(ORG_NAME)
    font = QFont("Segoe UI", 10)
    font.setStyleHint(QFont.SansSerif)
    app.setFont(font)
    apply_theme(app, "system")
    return app


def use_saved_theme(app, s) -> None:
    apply_theme(app, s.db.get_setting("theme", "system") or "system")
    try:
        app.styleHints().colorSchemeChanged.disconnect()
    except Exception:  # noqa: BLE001
        pass
    try:
        app.styleHints().colorSchemeChanged.connect(
            lambda *_: apply_theme(app, "system") if current_mode() == "system" else None)
    except Exception:  # noqa: BLE001
        pass


def restart_main(old_window, user, page: str | None = None):
    """Rebuild the main window (another user signed in, or the theme changed)."""
    from .main_window import MainWindow
    s = old_window.s
    old_window.deleteLater()
    win = MainWindow(s, user)
    _windows.clear()
    _windows.append(win)
    win.showMaximized()
    if page:
        win.go(page)


def connect(app, conn: conn_mod.Connection | None) -> Services | None:
    """Open the store database, asking for settings or a retry until it works or the person gives up."""
    from .setup_dialogs import ConnectionDialog
    from .widgets.common import set_server_db
    while True:
        if conn is None:
            d = ConnectionDialog()
            if d.exec() != QDialog.Accepted or d.result_conn is None:
                return None
            conn = d.result_conn
        try:
            s = Services(conn.conninfo(), station_id=conn.station_id, timezone=conn.timezone)
            s.conninfo = conn.conninfo()
            s.connection = conn
            set_server_db(s.db)
            return s
        except ServerUnavailable as e:
            box = QMessageBox()
            box.setIcon(QMessageBox.Warning)
            box.setWindowTitle(f"{APP_NAME} - server not reachable")
            box.setText(f"{e}\n\nStation {conn.station_id} could not open the store database "
                        f"'{conn.dbname}' on {conn.host}.")
            retry = box.addButton("Retry", QMessageBox.AcceptRole)
            change = box.addButton("Change settings", QMessageBox.ActionRole)
            box.addButton("Quit", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is retry:
                continue
            if box.clickedButton() is change:
                d = ConnectionDialog(conn)
                if d.exec() != QDialog.Accepted or d.result_conn is None:
                    return None
                conn = d.result_conn
                continue
            return None
        except RuntimeError as e:          # e.g. data from a newer OwnPOS
            QMessageBox.critical(None, APP_NAME, str(e))
            return None


def sign_in(app, s: Services):
    """Station registration, licence, first run and login. Returns (user, can_sell) or None."""
    from ..core.licence import status
    from ..core.stations import register
    from .login import FirstRunDialog, LoginDialog
    from .setup_dialogs import ActivationDialog
    use_saved_theme(app, s)
    conn = getattr(s, "connection", None)
    reg = register(s.db, s.db.station_id, conn.station_name if conn else "")
    can_sell = reg["can_sell"]
    st = status(s.db)
    training_setup = not s.auth.has_users() and "demo" in (getattr(conn, "dbname", "") or "")
    if not st.can_sell and st.state in ("missing", "invalid") and not training_setup:
        d = ActivationDialog(s, st)
        if d.exec() != QDialog.Accepted:
            return None
        st = status(s.db)
        reg = register(s.db, s.db.station_id, conn.station_name if conn else "")
        can_sell = reg["can_sell"]
    can_sell = can_sell and st.can_sell
    s.licence_banner = st.banner or reg["message"]
    if not s.auth.has_users():
        if FirstRunDialog(s, offer_sample=training_setup).exec() != QDialog.Accepted:
            return None
        if training_setup:
            st = status(s.db)                  # a filled training database is now "demo"
            if not st.can_sell and st.state in ("missing", "invalid"):   # sample declined: a normal store
                d = ActivationDialog(s, st)
                if d.exec() != QDialog.Accepted:
                    return None
                st = status(s.db)
            reg = register(s.db, s.db.station_id, conn.station_name if conn else "")
            can_sell = reg["can_sell"] and st.can_sell
            s.licence_banner = st.banner or reg["message"]
    d = LoginDialog(s)
    if d.exec() != QDialog.Accepted:
        return None
    return d.user, can_sell


def self_test(report_path: str) -> int:
    """Start the full program on a throw-away store database, open every screen, run every report and write
    the result. Needs a PostgreSQL server: OWNPOS_SELFTEST_PG (e.g. "host=localhost user=postgres password=...").
    Without one, only checks that every module of the packaged program loads. Exit code 0 = good."""
    import traceback
    import uuid
    lines, code = [], 0
    try:
        from .. import APP_VERSION
        import importlib
        import pkgutil
        import ownpos
        for m in pkgutil.walk_packages(ownpos.__path__, "ownpos."):
            importlib.import_module(m.name)
        lines.append("ok all modules load")
        pg = os.environ.get("OWNPOS_SELFTEST_PG", "")
        if pg:
            import psycopg
            name = "ownpos_selftest_" + uuid.uuid4().hex[:8]
            with psycopg.connect(pg + " dbname=postgres", autocommit=True) as c:
                c.execute(f"CREATE DATABASE {name}")
            try:
                params = dict(p.split("=", 1) for p in pg.split())
                params["dbname"] = name
                s = Services(params, station_id=1)
                s.conninfo = params
                from ..core.demo import fill_sample
                from ..core.stations import register
                register(s.db, 1)
                register(s.db, 2)
                uid = s.auth.create_user("owner", "Self Test", "owner", password="selftest-1", pin="9999")
                fill_sample(s, uid, days=2, sales_per_day=30)
                from .main_window import NAV, MainWindow
                win = MainWindow(s, s.auth.get(uid), can_sell=True)
                for _g, _h, entries in NAV:
                    for key, _label in entries:
                        win.go(key)
                        QApplication.processEvents()
                        lines.append(f"ok page {key}")
                from ..core.reports import REGISTRY, run_report
                for key in REGISTRY:
                    run_report(s.db, key, {})
                lines.append(f"ok reports {len(REGISTRY)}")
                if not s.ledger.is_balanced():
                    raise RuntimeError("ledger not balanced")
                win.close()
                s.close()
            finally:
                with psycopg.connect(pg + " dbname=postgres", autocommit=True) as c:
                    c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s"
                              " AND pid <> pg_backend_pid()", (name,))
                    c.execute(f"DROP DATABASE IF EXISTS {name}")
        else:
            lines.append("no OWNPOS_SELFTEST_PG: screens and reports not opened (modules only)")
        lines.insert(0, f"SELFTEST OK {APP_VERSION}")
    except Exception:  # noqa: BLE001
        code = 1
        lines.insert(0, "SELFTEST FAILED")
        lines.append(traceback.format_exc())
    Path(report_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return code


def main(argv=None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    if "--allow-network" in argv:                 # the server setup's administrator step (see setup_dialogs)
        i = argv.index("--allow-network")
        hba, port, dbs, restart = argv[i + 1], int(argv[i + 2]), argv[i + 3].split(","), argv[i + 4] == "1"
        from ..core.paths import log_folder
        from ..core.serversetup import allow_network_windows
        done = allow_network_windows(hba, port, dbs, restart=restart)
        (log_folder() / "server-setup.log").write_text("\n".join(done), encoding="utf-8")
        return 0
    if "--self-test" in argv:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        i = argv.index("--self-test")
        report = argv[i + 1] if len(argv) > i + 1 else "selftest.txt"
        make_app(argv)
        return self_test(report)
    app = make_app(argv)
    app.setQuitOnLastWindowClosed(False)
    s = connect(app, conn_mod.load())
    if s is None:
        return 0
    result = sign_in(app, s)
    if result is None:
        s.close()
        return 0
    user, can_sell = result
    _open["services"] = s
    from .main_window import MainWindow
    win = MainWindow(s, user, can_sell=can_sell)
    _windows.append(win)
    win.showMaximized()
    app.setQuitOnLastWindowClosed(True)
    code = app.exec()
    if _open["services"] is not None:
        _open["services"].close()
    return code
