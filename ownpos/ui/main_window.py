"""Main window: sidebar navigation, pages, status bar, and app-wide helpers
(printing, drawer, scale, shift checks, idle lock, agent timers)."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import (QButtonGroup, QDialog, QFormLayout, QFrame, QHBoxLayout, QLabel, QMainWindow,
                               QPushButton, QScrollArea, QStackedWidget, QStatusBar, QVBoxLayout, QWidget)

from .. import APP_NAME, APP_VERSION
from ..core.auth import audit
from ..core.db import CLOCK_WARN_SECONDS, clock_offset_seconds, now_dt
from ..hardware import devices
from . import printing
from .branding import logo_pixmap
from .widgets.common import MoneyEdit, button, error, guarded, info, label, rs

# The side toolbar, grouped the way QuickBooks Point of Sale groups its work: (group key, heading,
# [(page key, label), ...]). The owner can rename any entry in Settings > Menu names ("menu_names").
NAV = [
    ("pos", "POINT OF SALE", [("home", "Home"), ("register", "Make a Sale"), ("sales_history", "Sales History"),
                              ("customers", "Customers"), ("eod", "End of Day")]),
    ("inventory", "INVENTORY", [("items", "Item List"), ("inventory", "Receive & Adjust"),
                                ("purchasing_history", "Purchasing History"), ("vendors", "Vendors")]),
    ("reports", "REPORTS", [("reports", "Report Center")]),
    ("employees", "EMPLOYEES", [("employees", "Employees"), ("timeclock", "Time Clock")]),
    ("money", "ACCOUNTING", [("accounting", "Accounting")]),
    ("setup", "ADMIN", [("lists", "Lists"), ("agents", "Agents"), ("assistant", "Ask OwnPOS"),
                        ("settings", "Settings"), ("help", "Help")]),
]
PAGE_PERMS = {"register": "sell", "sales_history": "sell", "purchasing_history": "receive",
              "customers": "customers", "items": "items", "inventory": "receive",
              "vendors": "receive", "eod": "x_report", "reports": "reports", "accounting": "accounting",
              "lists": "lists", "employees": "employees", "agents": "agents", "assistant": "reports",
              "settings": "settings"}
OWNER_PAGES: set = set()


def menu_text(names: dict, key: str, default: str) -> str:
    """The owner's name for a menu entry or heading, or the default. (Buttons double '&' themselves.)"""
    text = (names or {}).get(key) or default
    return text.replace("&&", "&")


class ActivityFilter(QObject):
    def __init__(self, win):
        super().__init__()
        self.win = win

    def eventFilter(self, obj, ev):
        if ev.type() in (QEvent.KeyPress, QEvent.MouseButtonPress):
            self.win.last_activity = datetime.now()
        return False


class MainWindow(QMainWindow):
    def __init__(self, services, user, can_sell: bool = True):
        super().__init__()
        self.s = services
        self.user = user
        self.can_sell = can_sell
        self.pages: dict[str, QWidget] = {}
        self._holders: dict[str, QScrollArea] = {}
        self.last_activity = datetime.now()
        station = self.s.db.val("SELECT name FROM stations WHERE id = ?", (self.s.db.station_id,),
                                f"Station {self.s.db.station_id}")
        self.station_name = station
        self.setWindowTitle(f"{APP_NAME} — {self.s.db.get_setting('company_name', '')} — {station}")
        self.menu_names = self.s.db.get_setting("menu_names", {}) or {}
        self.resize(1366, 800)
        self.setMinimumSize(1000, 600)
        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._sidebar())
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.st_banner = QLabel()
        self.st_banner.setObjectName("notice")
        self.st_user = QLabel()
        self.st_shift = QLabel()
        self.st_date = QLabel()
        for w in (self.st_banner, self.st_user, self.st_shift, self.st_date):
            self.status.addPermanentWidget(w)
        self.refresh_status()
        self.clock = QTimer(self)
        self.clock.timeout.connect(self._tick)
        self.clock.start(30_000)
        self.hourly = QTimer(self)
        self.hourly.timeout.connect(self._hourly)
        self.hourly.start(3_600_000)
        QTimer.singleShot(2500, self._startup_agents)
        self._activity = ActivityFilter(self)
        from PySide6.QtWidgets import QApplication
        QApplication.instance().installEventFilter(self._activity)
        from PySide6.QtGui import QKeySequence, QShortcut
        f1 = QShortcut(QKeySequence("F1"), self)
        f1.setContext(Qt.ApplicationShortcut)
        f1.activated.connect(lambda: self.go("help"))
        # people who sell but do not see reports (cashiers) start on the sale screen
        self.go("register" if self.user.can("sell") and not self.user.can("reports") else "home")

    # ---- sidebar -----------------------------------------------------
    def _sidebar(self):
        side = QFrame()
        side.setObjectName("sidebar")
        self.sidebar = side
        lay = QVBoxLayout(side)
        lay.setContentsMargins(10, 14, 10, 10)
        lay.setSpacing(2)
        name = self.s.db.get_setting("company_name", APP_NAME) or APP_NAME
        logo = QLabel()
        logo.setPixmap(logo_pixmap(30))
        brand_row = QHBoxLayout()
        brand_row.setSpacing(8)
        brand_row.addWidget(logo)
        brand_row.addWidget(label(APP_NAME, "brand"), 1)
        lay.addLayout(brand_row)
        self.brand_sub = label(f"{name}\n{self.station_name}", "brandSub", wrap=True)
        lay.addWidget(self.brand_sub)
        if self.s.db.get_setting("is_demo"):
            band = label("SAMPLE STORE", "navGroup")
            band.setStyleSheet("color: #F4B942; font-weight: 700;")
            lay.addWidget(band)
        lay.addSpacing(8)
        self.nav_group = QButtonGroup(self)
        self.nav_buttons = {}
        for gkey, heading, entries in NAV:
            heading = menu_text(self.menu_names, "group:" + gkey, heading)
            visible = [(k, t) for k, t in entries if self._may_open(k)]
            if heading.strip() and visible:      # an empty heading = no heading above this group
                lay.addWidget(label(heading, "navGroup"))
            for key, text in visible:
                b = QPushButton(menu_text(self.menu_names, key, text).replace("&", "&&"))
                b.setObjectName("nav")
                b.setCheckable(True)
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, k=key: self.go(k))
                self.nav_group.addButton(b)
                self.nav_buttons[key] = b
                lay.addWidget(b)
        lay.addStretch(1)
        sw = QPushButton("Switch user / lock")
        sw.setObjectName("nav")
        sw.clicked.connect(self.lock)
        lay.addWidget(sw)
        # on short screens (1366x768) the menu gets tighter, and scrolls if it still does not fit
        scroll = QScrollArea()
        scroll.setObjectName("sideScroll")
        scroll.setWidget(side)
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(210)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.NoFrame)
        return scroll

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        h = self.height()
        compact = "tight" if h < 760 else ("true" if h < 950 else "false")
        if self.sidebar.property("compact") != compact:
            self.sidebar.setProperty("compact", compact)
            self.brand_sub.setVisible(compact != "tight")
            for w in [self.sidebar] + self.sidebar.findChildren(QWidget):
                w.style().unpolish(w)
                w.style().polish(w)

    def _may_open(self, key: str) -> bool:
        perm = PAGE_PERMS.get(key)
        if perm and not self.user.can(perm):
            return False
        if key in OWNER_PAGES and self.user.role != "owner":
            return False
        return True

    def cannot_sell_reason(self) -> str:
        return (getattr(self.s, "licence_banner", "") or
                "This station may not sell right now (licence). Reports, exports and backups still work.")

    def _make_page(self, key):
        from .pages import (accounting, agents_page, assistant_page, eod, help_page, history_page, home,
                            inventory_page, items_page, lists_page, register, reports_page, settings_page,
                            timeclock_page)
        factories = {
            "home": lambda: home.HomePage(self),
            "register": lambda: register.RegisterPage(self),
            "sales_history": lambda: history_page.SalesHistoryPage(self),
            "purchasing_history": lambda: history_page.PurchasingHistoryPage(self),
            "customers": lambda: lists_page.EntityPage(self, "customers"),
            "items": lambda: items_page.ItemsPage(self),
            "vendors": lambda: lists_page.EntityPage(self, "vendors"),
            "employees": lambda: lists_page.EmployeesPage(self),
            "timeclock": lambda: timeclock_page.TimeClockPage(self),
            "lists": lambda: lists_page.ListsHub(self),
            "inventory": lambda: inventory_page.InventoryPage(self),
            "eod": lambda: eod.EndOfDayPage(self),
            "reports": lambda: reports_page.ReportsPage(self),
            "accounting": lambda: accounting.AccountingPage(self),
            "agents": lambda: agents_page.AgentsPage(self),
            "assistant": lambda: assistant_page.AssistantPage(self),
            "settings": lambda: settings_page.SettingsPage(self),
            "help": lambda: help_page.HelpPage(self),
        }
        page = factories[key]()
        self.pages[key] = page
        # safety net: a page that cannot shrink to the window scrolls instead of pushing the window off-screen
        holder = QScrollArea()
        holder.setObjectName("pageScroll")
        holder.setWidget(page)
        holder.setWidgetResizable(True)
        holder.setFrameShape(QFrame.NoFrame)
        self._holders[key] = holder
        self.stack.addWidget(holder)
        return page

    @guarded
    def go(self, key: str):
        if not self._may_open(key):
            error(self, "Your role does not have access to that screen.")
            return
        page = self.pages.get(key) or self._make_page(key)
        if hasattr(page, "on_show"):
            page.on_show()                  # every screen re-reads the server when shown (other stations' changes)
        self.stack.setCurrentWidget(self._holders[key])
        if key in self.nav_buttons:
            self.nav_buttons[key].setChecked(True)

    # ---- status ------------------------------------------------------
    def refresh_status(self):
        banner = getattr(self.s, "licence_banner", "")
        off = clock_offset_seconds()
        if abs(off) > CLOCK_WARN_SECONDS:
            banner = (banner + "   " if banner else "") + (
                f"This PC's clock is {abs(off) / 60:.0f} min {'behind' if off > 0 else 'ahead of'} the server; "
                "OwnPOS uses the server's time.")
        self.st_banner.setText(f"  {banner}  " if banner else "")
        self.st_user.setText(f"  {self.user.full_name} ({self.s.auth.role_name(self.user.role)})  ")
        try:
            sh = self.s.shifts.current()
        except Exception:  # noqa: BLE001 - the server may be away; the status bar must not crash the window
            sh = None
        self.st_shift.setText(f"  Drawer open since {sh['opened_at'][11:16]}  " if sh else "  Drawer not open  ")
        now = now_dt()
        self.st_date.setText(f"  {self.station_name} · {now:%a %m/%d/%Y %I:%M %p}  ")

    def toast(self, text: str, ms: int = 6000):
        self.status.showMessage(text, ms)

    def _hourly(self):
        try:
            self.s.db.sync_clock()
            self.s.agents.run_schedule("hourly")
        except Exception:  # noqa: BLE001 - never interrupt selling for a background job
            pass

    def _tick(self):
        self.refresh_status()
        mins = int(self.s.db.get_setting("idle_lock_minutes", 10) or 0)
        if mins and (datetime.now() - self.last_activity).total_seconds() > mins * 60:
            self.last_activity = datetime.now()
            self.lock()

    def _startup_agents(self):
        try:
            self.s.agents.run_schedule("startup")
        except Exception:  # noqa: BLE001
            pass
        finally:
            if "home" in self.pages:
                self.pages["home"].on_show()

    def lock(self):
        from .login import LoginDialog
        audit(self.s.db, self.user.id, "lock", "user", self.user.username, {})
        self.hide()
        d = LoginDialog(self.s, "Unlock", lock=True)
        if d.exec() != QDialog.Accepted:
            from PySide6.QtWidgets import QApplication
            QApplication.instance().quit()
            return
        if d.user.id != self.user.id:
            # different user: rebuild the window so menus match their role
            from .app import restart_main
            restart_main(self, d.user)
            return
        self.show()

    # ---- shifts ------------------------------------------------------
    def ensure_shift(self) -> bool:
        if not self.s.db.get_setting("require_shift", True) or self.s.shifts.current():
            return True
        if not self.user.can("shift"):
            error(self, "This station's drawer is not open. Ask a manager to open it.")
            return False
        d = QDialog(self)
        d.setWindowTitle(f"Open the drawer - {self.station_name}")
        key = f"default_float_station_{self.s.db.station_id}"
        amt = MoneyEdit(int(self.s.db.get_setting(key, 15000) or 0))
        amt.setObjectName("amount")
        lay = QVBoxLayout(d)
        lay.setContentsMargins(18, 16, 18, 16)
        lay.addWidget(label("Count the cash in this station's drawer to start.", "sectionTitle"))
        form = QFormLayout()
        form.addRow("Opening cash ($)", amt)
        lay.addLayout(form)
        ok = button("Open drawer", "primary", d.accept)
        lay.addWidget(ok)
        if d.exec() != QDialog.Accepted:
            return False
        self.s.shifts.open(self.user.id, amt.cents())
        self.s.db.set_setting(key, amt.cents())
        self.refresh_status()
        return True

    # ---- devices -----------------------------------------------------
    def print_receipt(self, sale_id: int, force: bool = False):
        st = self.s.db.settings()
        if not force and not st.get("receipt_auto_print", True):
            self.s.sales.record_print(sale_id, self.user.id)
            return
        copy_no = self.s.sales.record_print(sale_id, self.user.id)
        from ..core.receipt import receipt_html
        html = receipt_html(self.s.db, sale_id, copy_no)
        width = int(st.get("receipt_width_mm", 80))
        printer = self.s.db.val("SELECT receipt_printer FROM stations WHERE id = ?", (self.s.db.station_id,)) or ""
        ok = False
        try:
            ok = printing.print_html(html, printer, width)
        except Exception as exc:  # noqa: BLE001
            self.toast(f"Printer problem: {exc}")
        if not ok:
            printing.preview_html(self, html, width, "Receipt (no printer found)")

    def open_drawer(self):
        from ..ui.branding import ui_settings
        local = ui_settings()            # drawer and display are per computer
        printer = self.s.db.val("SELECT receipt_printer FROM stations WHERE id = ?", (self.s.db.station_id,)) or ""
        port = str(local.value("drawer_port", "") or "")
        ok, msg = devices.open_drawer(str(local.value("drawer_printer", "") or printer), port)
        if not ok and (printer or port):
            self.toast(msg)

    def show_customer_display(self, line1: str, line2: str):
        from ..ui.branding import ui_settings
        port = str(ui_settings().value("customer_display_port", "") or "")
        if port:
            devices.show_on_display(port, line1, line2)

    def closeEvent(self, ev):
        try:
            audit(self.s.db, self.user.id, "logout", "user", self.user.username, {})
        except Exception:  # noqa: BLE001 - closing must always work, even with the server away
            pass
        super().closeEvent(ev)
