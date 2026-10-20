"""First-run store setup, and the sign-in / lock screen with the time clock."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QGridLayout, QLabel, QLineEdit, QTabWidget,
                               QVBoxLayout, QWidget)

from .. import APP_NAME, APP_VERSION
from .branding import logo_pixmap
from .widgets.common import button, error, guarded, hbox, label, vbox


class FirstRunDialog(QDialog):
    """Store details and the owner account, shown when the store database has no employees yet."""
    def __init__(self, s, offer_sample: bool = False):
        super().__init__()
        self.s = s
        self.setWindowTitle(f"Welcome to {APP_NAME}")
        self.setMinimumWidth(560)
        st = s.db.settings()
        self.name = QLineEdit(st.get("company_name", ""))
        self.name.setPlaceholderText("The store's name, as it should print on receipts")
        self.addr = QLineEdit(st.get("company_address", ""))
        self.city = QLineEdit(st.get("company_city_state_zip", ""))
        self.city.setPlaceholderText("City, IN ZIP")
        self.phone = QLineEdit(st.get("company_phone", ""))
        self.permit = QLineEdit(st.get("company_permit_no", ""))
        self.permit.setPlaceholderText("Optional - Indiana ATC permit number, printed on receipts if given")
        self.freq = QComboBox()
        for code, text in (("quarterly", "Quarterly"), ("monthly", "Monthly"), ("annual", "Annually")):
            self.freq.addItem(text, code)
        self.freq.setCurrentIndex(max(0, self.freq.findData(st.get("sales_tax_frequency", "quarterly"))))
        self.user = QLineEdit("owner")
        self.full = QLineEdit()
        self.full.setPlaceholderText("Your full name")
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("At least 8 characters")
        self.pin = QLineEdit()
        self.pin.setEchoMode(QLineEdit.Password)
        self.pin.setPlaceholderText("4 to 8 digits for quick sign-in at the register")
        from PySide6.QtWidgets import QCheckBox
        self.sample = QCheckBox("This is the TRAINING database: fill it with the sample store (items, vendors, "
                                "two weeks of sales). Every receipt then says TRAINING.")
        self.sample.setChecked(offer_sample)
        self.sample.setVisible(offer_sample)
        form = QFormLayout()
        form.addRow(self.sample)
        form.addRow(label("Your store", "sectionTitle"))
        form.addRow("Store name", self.name)
        form.addRow("Street address", self.addr)
        form.addRow("City, state, ZIP", self.city)
        form.addRow("Phone", self.phone)
        form.addRow("Permit number", self.permit)
        form.addRow("Sales tax is filed", self.freq)
        form.addRow(label("Owner login", "sectionTitle"))
        form.addRow("Login name", self.user)
        form.addRow("Full name", self.full)
        form.addRow("Password", self.pw)
        form.addRow("PIN", self.pin)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 18)
        lay.addWidget(label(f"Set up {APP_NAME}", "pageTitle"))
        lay.addWidget(label("These details print on every receipt. You can change them later in Settings.", "muted"))
        lay.addLayout(form)
        lay.addLayout(hbox(None, button("Start", "primary", self.save)))

    def save(self):
        if not self.name.text().strip() and not self.sample.isChecked():
            error(self, "Enter the store name")
            return
        if len(self.pw.text()) < 8:
            error(self, "Choose a password of at least 8 characters")
            return
        if self.pin.text() and (not self.pin.text().isdigit() or not 4 <= len(self.pin.text()) <= 8):
            error(self, "The PIN must be 4 to 8 digits")
            return
        db = self.s.db
        for k, w in (("company_name", self.name), ("company_address", self.addr), ("company_city_state_zip", self.city),
                     ("company_phone", self.phone), ("company_permit_no", self.permit)):
            db.set_setting(k, w.text().strip())
        db.set_setting("sales_tax_frequency", self.freq.currentData())
        try:
            uid = self.s.auth.create_user(self.user.text().strip() or "owner", self.full.text().strip() or "Owner",
                                          "owner", password=self.pw.text(), pin=self.pin.text())
        except Exception as exc:  # noqa: BLE001
            error(self, str(exc))
            return
        if self.sample.isChecked():
            from PySide6.QtWidgets import QApplication
            from ..core.demo import fill_sample
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                fill_sample(self.s, uid, days=14, sales_per_day=60)
            except Exception as exc:  # noqa: BLE001
                QApplication.restoreOverrideCursor()
                error(self, f"The sample store could not be made: {exc}")
                return
            QApplication.restoreOverrideCursor()
        self.accept()


class LoginDialog(QDialog):
    """Sign in with a PIN or a password. The same PIN pad clocks employees in and out."""

    def __init__(self, s, title: str = "Sign in", lock: bool = False):
        super().__init__()
        self.s = s
        self.user = None
        self.setWindowTitle(f"{APP_NAME} · {title}")
        self.setMinimumWidth(440)
        st = s.db.settings()
        tabs = QTabWidget()
        pin_w = QWidget()
        self.pin = QLineEdit()
        self.pin.setEchoMode(QLineEdit.Password)
        self.pin.setObjectName("amount")
        self.pin.setAlignment(Qt.AlignCenter)
        self.pin.returnPressed.connect(self.do_pin)
        pad = QGridLayout()
        for i, k in enumerate(["1", "2", "3", "4", "5", "6", "7", "8", "9", "C", "0", "⌫"]):
            b = button(k, "key", lambda _=False, k=k: self._key(k))
            b.setMinimumHeight(52)
            pad.addWidget(b, i // 3, i % 3)
        sign = button("Sign in", "primary", self.do_pin)
        sign.setMinimumHeight(46)
        cin = button("Clock in", slot=lambda: self.punch(True))
        cout = button("Clock out", slot=lambda: self.punch(False))
        for b in (cin, cout):
            b.setMinimumHeight(40)
        pin_w.setLayout(vbox(label("Enter your PIN"), self.pin, pad, sign, hbox(cin, cout), margins=(12, 12, 12, 12)))
        pw_w = QWidget()
        self.username = QLineEdit()
        self.password = QLineEdit()
        self.password.setEchoMode(QLineEdit.Password)
        self.password.returnPressed.connect(self.do_password)
        form = QFormLayout()
        form.addRow("Login name", self.username)
        form.addRow("Password", self.password)
        pw_w.setLayout(vbox(form, hbox(None, button("Sign in", "primary", self.do_password)), margins=(12, 12, 12, 12)))
        tabs.addTab(pin_w, "PIN")
        tabs.addTab(pw_w, "Password")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(40))
        station = s.db.val("SELECT name FROM stations WHERE id = ?", (s.db.station_id,), f"Station {s.db.station_id}")
        lay.addLayout(hbox(logo, vbox(label(st.get("company_name", APP_NAME) or APP_NAME, "pageTitle"),
                                      label(("Screen locked · " if lock else "") + f"{station} · {APP_NAME} {APP_VERSION}",
                                            "muted"), spacing=0), None))
        if st.get("is_demo"):
            lay.addWidget(label("SAMPLE STORE - for training. Nothing here is real.", "notice"))
        lay.addWidget(tabs)
        self.msg = label("", "muted", wrap=True)
        lay.addWidget(self.msg)
        self.who = label("", "muted", wrap=True)
        lay.addWidget(self.who)
        self._show_who()
        self.pin.setFocus()
        self._clear = QTimer(self)
        self._clear.setSingleShot(True)
        self._clear.timeout.connect(lambda: self.msg.setText(""))

    def _show_who(self):
        try:
            rows = self.s.timeclock.who_is_in()
        except Exception:  # noqa: BLE001
            rows = []
        self.who.setText("Clocked in: " + ", ".join(f"{r['full_name']} (since {r['clock_in'][11:16]})" for r in rows)
                         if rows else "Nobody is clocked in.")

    def _key(self, k):
        if k == "C":
            self.pin.clear()
        elif k == "⌫":
            self.pin.backspace()
        else:
            self.pin.insert(k)

    def _say(self, text: str):
        self.msg.setText(text)
        self._clear.start(8000)

    @guarded
    def punch(self, clock_in: bool):
        u = self.s.auth.login_pin(self.pin.text())
        self.pin.clear()
        if u is None:
            self._say("Wrong PIN")
            return
        from ..core.timeclock import TimeClockError
        try:
            if clock_in:
                self.s.timeclock.clock_in(u.id)
                self._say(f"{u.full_name}: clocked in.")
            else:
                r = self.s.timeclock.clock_out(u.id)
                self._say(f"{u.full_name}: clocked out after {r['hours']:.2f} hours.")
        except TimeClockError as e:
            self._say(f"{u.full_name}: {e}")
        self._show_who()

    @guarded
    def do_pin(self):
        u = self.s.auth.login_pin(self.pin.text())
        if u:
            self.user = u
            self.accept()
        else:
            self.pin.clear()
            self._say("Wrong PIN")

    @guarded
    def do_password(self):
        u = self.s.auth.login(self.username.text(), self.password.text())
        if u:
            self.user = u
            self.accept()
        else:
            self.password.clear()
            self._say("Wrong login name or password")
