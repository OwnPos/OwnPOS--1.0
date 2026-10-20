"""Small shared widgets and helpers."""
from __future__ import annotations

import functools
import traceback
from datetime import date
from typing import Sequence

from PySide6.QtCore import QDate, QSize, Qt, Signal
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import (QAbstractButton, QCheckBox, QComboBox, QDateEdit, QDialog, QDialogButtonBox, QFrame,
                               QHBoxLayout, QLabel, QLineEdit, QMenu, QMessageBox, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from ... import APP_NAME
from ...core.db import CommitUnknown, ServerUnavailable
from ...core.money import fmt_money, to_cents


def info(parent, text: str, title: str = APP_NAME):
    QMessageBox.information(parent, title, text)


def error(parent, text: str, title: str = "Something needs attention"):
    QMessageBox.warning(parent, title, text)


def confirm(parent, text: str, title: str = "Please confirm") -> bool:
    return QMessageBox.question(parent, title, text, QMessageBox.Yes | QMessageBox.No, QMessageBox.No) == QMessageBox.Yes


_server_db = {"db": None}


def set_server_db(db) -> None:
    """The station's database connection, so a lost connection can be re-made from any screen."""
    _server_db["db"] = db


def server_down(parent, exc: Exception) -> bool:
    """The server or network is gone: say so plainly and let the cashier retry. Returns True when connected
    again. Nothing is saved on this computer meanwhile (decision 16 Oct 2026); the sale on screen stays."""
    db = _server_db["db"]
    unknown = isinstance(exc, CommitUnknown)
    while True:
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Server not reachable - sales are paused")
        text = (str(exc) + "\n\n" if str(exc) else "") + (
            "This station cannot reach the store server, so nothing can be saved right now. "
            "The sale on the screen is kept.\n\nCheck that the server PC is on and this computer's network "
            "cable / Wi-Fi is connected, then press Retry.")
        if unknown:
            text += ("\n\nThe connection broke while saving. After reconnecting, OwnPOS checks whether that "
                     "sale was saved before saving it again, so it is never charged twice.")
        box.setText(text)
        retry = box.addButton("Retry", QMessageBox.AcceptRole)
        box.addButton("Wait", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is not retry or db is None:
            return False
        try:
            db.reconnect()
            return True
        except ServerUnavailable as e2:
            exc = e2


def guarded(fn):
    """Show any exception from a UI action as a friendly message instead of crashing."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            return fn(self, *args, **kwargs)
        except ServerUnavailable as exc:
            server_down(self if isinstance(self, QWidget) else None, exc)
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            error(self if isinstance(self, QWidget) else None, str(exc))
    return wrapper


def button(text: str, kind: str = "", slot=None, tip: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setObjectName(kind)
    if slot:
        b.clicked.connect(slot)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.PointingHandCursor)
    return b


def label(text: str = "", name: str = "", wrap: bool = False) -> QLabel:
    lb = QLabel(text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


def panel() -> QFrame:
    f = QFrame()
    f.setObjectName("panel")
    return f


def hbox(*widgets, spacing=8, margins=(0, 0, 0, 0), stretch_last=False) -> QHBoxLayout:
    lay = QHBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return lay


def vbox(*widgets, spacing=8, margins=(0, 0, 0, 0)) -> QVBoxLayout:
    lay = QVBoxLayout()
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in widgets:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, QWidget):
            lay.addWidget(w)
        else:
            lay.addLayout(w)
    return lay


class ActionBar(QWidget):
    """A row of buttons that never forces the window wider than the screen.

    It asks for room for every widget, but when the page is narrow (1366 px
    screens) the buttons at the right-hand end fold into a "More" menu. The
    first `keep` foldable buttons always stay visible. Widgets that cannot be
    shown as a menu entry (e.g. a combo box) always stay visible too.
    Buttons, check boxes and combo boxes appear in the menu with their current
    enabled / checked state.
    """

    def __init__(self, widgets, keep: int = 1, spacing: int = 8, parent=None):
        super().__init__(parent)
        self.widgets = [w for w in widgets if w is not None]
        self.keep = keep
        self.spacing = spacing
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(spacing)
        for w in self.widgets:
            lay.addWidget(w)
        self.more = QPushButton("More  ▾")
        self.more.setCursor(Qt.PointingHandCursor)
        self.more.setToolTip("More actions")
        self.menu = QMenu(self.more)
        self.menu.aboutToShow.connect(self._fill_menu)
        self.more.setMenu(self.menu)
        self.more.setStyleSheet("QPushButton::menu-indicator { width: 0px; }")
        lay.addWidget(self.more)
        self.more.hide()
        self.folded: list[QWidget] = []
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    @staticmethod
    def _foldable(w) -> bool:
        return isinstance(w, (QAbstractButton, QComboBox))

    def _w(self, w) -> int:
        return w.sizeHint().width()

    def sizeHint(self) -> QSize:
        ws = [self._w(w) for w in self.widgets]
        h = max([w.sizeHint().height() for w in self.widgets] + [self.more.sizeHint().height()])
        return QSize(sum(ws) + self.spacing * max(len(ws) - 1, 0), h)

    def minimumSizeHint(self) -> QSize:
        fixed, kept = [], 0
        for w in self.widgets:
            if not self._foldable(w):
                fixed.append(self._w(w))
            elif kept < self.keep:
                fixed.append(self._w(w))
                kept += 1
        fixed.append(self._w(self.more))
        return QSize(sum(fixed) + self.spacing * max(len(fixed) - 1, 0), self.sizeHint().height())

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._relayout()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._relayout()

    def _relayout(self):
        avail = self.width()
        if self.sizeHint().width() <= avail:
            fold = []
        else:
            used = self._w(self.more)
            fold, kept = [], 0
            # keep widgets from the left while they fit; fold the rest
            for w in self.widgets:
                need = self._w(w) + self.spacing
                must = not self._foldable(w) or kept < self.keep
                if must or (not fold and used + need <= avail):
                    used += need
                    if self._foldable(w):
                        kept += 1
                else:
                    fold.append(w)
        if fold == self.folded and self.more.isVisible() == bool(fold):
            return
        self.folded = fold
        for w in self.widgets:
            w.setVisible(w not in fold)
        self.more.setVisible(bool(fold))

    def _fill_menu(self):
        self.menu.clear()
        for w in self.folded:
            if isinstance(w, QComboBox):
                sub = self.menu.addMenu(w.toolTip() or "Show")
                for i in range(w.count()):
                    act = sub.addAction(w.itemText(i))
                    act.setCheckable(True)
                    act.setChecked(i == w.currentIndex())
                    act.triggered.connect(lambda _=False, w=w, i=i: w.setCurrentIndex(i))
                continue
            text = w.text().replace("&&", "\x00").replace("&", "").replace("\x00", "&")
            if isinstance(w, QPushButton) and w.menu() is not None:
                sub = self.menu.addMenu(text)
                for act in w.menu().actions():
                    sub.addAction(act)
                continue
            act = self.menu.addAction(text)
            act.setEnabled(w.isEnabled())
            if w.toolTip():
                act.setToolTip(w.toolTip())
            if isinstance(w, QCheckBox) or w.isCheckable():
                act.setCheckable(True)
                act.setChecked(w.isChecked())
                act.triggered.connect(lambda on, w=w: w.setChecked(on))
            else:
                act.triggered.connect(lambda _=False, w=w: w.click())


def page_header(title: str, subtitle: str = "", *actions) -> QHBoxLayout:
    """Title, a subtitle that wraps instead of widening the page, and actions that fold into "More"."""
    sub = label(subtitle, "pageSub", wrap=True) if subtitle else None
    if sub is not None:
        sub.setMinimumWidth(400)
    left = vbox(label(title, "pageTitle"), sub, spacing=2)
    lay = QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(16)
    lay.addLayout(left, 1)
    if actions:
        bar = ActionBar(actions)
        lay.addWidget(bar, 0, Qt.AlignTop)
    return lay


class MoneyEdit(QLineEdit):
    """Dollar entry that returns cents."""
    def __init__(self, cents: int = 0, parent=None):
        super().__init__(parent)
        v = QDoubleValidator(-1e12, 1e12, 2, self)
        v.setNotation(QDoubleValidator.StandardNotation)
        self.setValidator(v)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.set_cents(cents)

    def cents(self) -> int:
        return to_cents(self.text() or "0")

    def set_cents(self, c: int):
        self.setText("" if c is None else f"{c / 100:.2f}")

    # older names used by screens shared with the original program
    paisa = cents
    set_paisa = set_cents


class QtyEdit(QLineEdit):
    def __init__(self, value: float = 0, decimals: int = 3, parent=None):
        super().__init__(parent)
        v = QDoubleValidator(0, 1e9, decimals, self)
        v.setNotation(QDoubleValidator.StandardNotation)
        self.setValidator(v)
        self.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        if value:
            self.setText(f"{value:g}")

    def value(self) -> float:
        try:
            return float(self.text() or 0)
        except ValueError:
            return 0.0


class DateEdit(QWidget):
    """Date picker shown the U.S. way (MM/DD/YYYY) with the weekday beside it; values are ISO dates."""
    changed = Signal()

    def __init__(self, d: date | None = None, parent=None):
        super().__init__(parent)
        self.edit = QDateEdit()
        self.edit.setCalendarPopup(True)
        self.edit.setDisplayFormat("MM/dd/yyyy")
        self.bs = label("", "muted")
        lay = hbox(self.edit, self.bs, spacing=6)
        self.setLayout(lay)
        self.edit.dateChanged.connect(self._update)
        if d is None:
            from ...core.db import today_str
            d = date.fromisoformat(today_str())
        self.set_date(d)

    def _update(self):
        try:
            self.bs.setText(self.date().strftime("%A"))
        except Exception:
            self.bs.setText("")
        self.changed.emit()

    def set_date(self, d: date):
        self.edit.setDate(QDate(d.year, d.month, d.day))
        self._update()

    def date(self) -> date:
        q = self.edit.date()
        return date(q.year(), q.month(), q.day())

    def iso(self) -> str:
        return self.date().isoformat()


NEW_ENTRY = "__type_for_new__"      # the data of the "+ New…" line at the end of a drop-down


BSDateEdit = DateEdit          # name used by screens shared with the original program


class RefCombo(QComboBox):
    """Drop-down over (value, label) pairs.

    With on_new (a function text -> (value, label) or None) it becomes a "Type for New" list: you can type
    to search, and when what you typed is not in the list (or you pick "+ New…" at the bottom) on_new opens
    a small form to add it. The new entry is then selected."""
    def __init__(self, choices: Sequence[tuple[object, str]] = (), blank: str | None = None, parent=None,
                 on_new=None, new_label: str = "+ New…"):
        super().__init__(parent)
        self.on_new = on_new
        self.new_label = new_label
        self._blank = blank
        self._busy = False
        self._last = None
        if on_new is not None:
            from PySide6.QtWidgets import QCompleter
            self.setEditable(True)
            self.setInsertPolicy(QComboBox.NoInsert)
            comp = self.completer()
            comp.setCaseSensitivity(Qt.CaseInsensitive)
            comp.setFilterMode(Qt.MatchContains)
            comp.setCompletionMode(QCompleter.PopupCompletion)
            self.lineEdit().editingFinished.connect(self._typed)
            self.activated.connect(self._activated)
        self.currentIndexChanged.connect(self._remember)
        self.set_choices(choices, blank)

    def set_choices(self, choices, blank=None):
        cur = self.value() if self.count() else None
        self.clear()
        if blank is not None:
            self.addItem(blank, None)
        for v, lbl in choices:
            self.addItem(str(lbl), v)
        if self.on_new is not None:
            self.addItem(self.new_label, NEW_ENTRY)
            if self.currentData() == NEW_ENTRY:      # an empty list: show nothing rather than "+ New…"
                self.setCurrentIndex(-1)
        if cur is not None:
            self.set_value(cur)
        self._remember()

    def value(self):
        data = self.currentData()
        return None if data == NEW_ENTRY else data

    def set_value(self, v):
        for i in range(self.count()):
            if self.itemData(i) == v or str(self.itemData(i)) == str(v):
                self.setCurrentIndex(i)
                return

    # ---- Type for New ------------------------------------------------------
    def _remember(self, *_):
        if self.currentData() != NEW_ENTRY and not self._busy:
            self._last = self.currentIndex()

    def _restore(self):
        self.setCurrentIndex(self._last if self._last is not None and self._last >= 0 else -1)

    def _activated(self, i):
        if self.itemData(i) == NEW_ENTRY:
            self._create("")

    def _typed(self):
        if self._busy:
            return
        text = self.currentText().strip()
        if not text or text == self.new_label:
            if not text and self._blank is not None:
                self.setCurrentIndex(0)
            return
        for i in range(self.count()):
            if self.itemData(i) != NEW_ENTRY and self.itemText(i).strip().lower() == text.lower():
                self.setCurrentIndex(i)
                return
        self._create(text)

    def _create(self, text: str):
        self._busy = True
        try:
            made = self.on_new(text)
        finally:
            self._busy = False
        if not made:
            self._restore()
            return
        value, label_ = made
        pos = self.count() - 1          # just above "+ New…"
        self.insertItem(pos, str(label_), value)
        self.setCurrentIndex(pos)
        self._remember()


class ElidedLabel(QLabel):
    """One-line label that shrinks with "…" instead of widening its parent; the full text is the tooltip."""
    def __init__(self, text: str = "", name: str = "", min_width: int = 40):
        super().__init__(text)
        if name:
            self.setObjectName(name)
        self._min_w = min_width
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

    def minimumSizeHint(self) -> QSize:
        return QSize(self._min_w, super().minimumSizeHint().height())

    def sizeHint(self) -> QSize:
        return QSize(self.fontMetrics().horizontalAdvance(self.text()) + 4, super().sizeHint().height())

    def paintEvent(self, ev):
        from PySide6.QtGui import QPainter
        p = QPainter(self)
        r = self.contentsRect()
        full = self.text()
        shown = self.fontMetrics().elidedText(full, Qt.ElideRight, r.width())
        self.setToolTip(full if shown != full else "")
        p.setPen(self.palette().color(self.foregroundRole()))
        p.setFont(self.font())
        p.drawText(r, int(self.alignment() | Qt.AlignVCenter), shown)


class KpiTile(QFrame):
    def __init__(self, title: str, value: str = "-", sub: str = ""):
        super().__init__()
        self.setObjectName("panel")
        self.value = ElidedLabel(value, "kpiValue")
        self.title = ElidedLabel(title, "kpiLabel")
        self.sub = ElidedLabel(sub, "muted")
        lay = vbox(self.title, self.value, self.sub, spacing=2, margins=(14, 12, 14, 12))
        self.setLayout(lay)

    def set(self, value: str, sub: str = ""):
        self.value.setText(value)
        self.sub.setText(sub)


class SecretDialog(QDialog):
    """Ask for a manager PIN or password (override) or a reason."""
    def __init__(self, parent, title: str, prompt: str, secret: bool = True):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.edit = QLineEdit()
        if secret:
            self.edit.setEchoMode(QLineEdit.Password)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        self.setLayout(vbox(label(prompt, wrap=True), self.edit, bb, margins=(16, 16, 16, 16)))
        self.setMinimumWidth(360)

    def text(self) -> str:
        return self.edit.text()


def ask_text(parent, title: str, prompt: str, default: str = "") -> str | None:
    d = SecretDialog(parent, title, prompt, secret=False)
    d.edit.setText(default)
    return d.text().strip() if d.exec() == QDialog.Accepted else None


def require(window, perm: str, what: str):
    """Return the user allowed to do `perm`: the current user, or a manager who enters their PIN."""
    user = window.user
    if user.can(perm):
        return user
    d = SecretDialog(window, "Manager approval", f"{what} needs a manager. Ask a manager to enter their PIN or password.")
    if d.exec() != QDialog.Accepted:
        return None
    approver = window.s.auth.authorize(d.text(), perm)
    if approver is None:
        error(window, "That PIN does not belong to someone allowed to approve this.")
        return None
    from ...core.auth import audit
    audit(window.s.db, approver.id, "override", "permission", perm, {"for_user": user.username, "what": what})
    return approver


def usd(cents) -> str:
    return fmt_money(cents or 0, "$")


rs = usd                         # name used by screens shared with the original program
