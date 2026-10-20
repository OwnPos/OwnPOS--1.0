"""Dialogs used by the register and back office."""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QPushButton, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
                               QHeaderView)

from ..core.money import fmt_money, fmt_qty, round_qty
from .theme import pal
from .widgets.common import MoneyEdit, QtyEdit, RefCombo, button, error, hbox, label, rs, vbox


class BaseDialog(QDialog):
    def __init__(self, parent, title: str, width: int = 460):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(width)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(18, 16, 18, 16)
        self.lay.setSpacing(10)

    def buttons(self, ok_text="OK", cancel=True):
        bb = QDialogButtonBox()
        ok = bb.addButton(ok_text, QDialogButtonBox.AcceptRole)
        ok.setObjectName("primary")
        if cancel:
            bb.addButton("Cancel", QDialogButtonBox.RejectRole)
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        self.lay.addWidget(bb)
        return ok

    def _accept(self):
        if self.validate():
            self.accept()

    def validate(self) -> bool:
        return True


# ---------------------------------------------------------------- line edit
class NumberDialog(BaseDialog):
    def __init__(self, parent, title: str, prompt: str, value: str = "", money: bool = False):
        super().__init__(parent, title, 360)
        self.edit = MoneyEdit(0) if money else QtyEdit()
        self.edit.setObjectName("amount")
        self.edit.setText(value)
        self.edit.selectAll()
        self.lay.addWidget(label(prompt))
        self.lay.addWidget(self.edit)
        pad = QGridLayout()
        keys = ["7", "8", "9", "4", "5", "6", "1", "2", "3", ".", "0", "⌫"]
        for i, k in enumerate(keys):
            b = button(k, "key", lambda _=False, k=k: self._key(k))
            pad.addWidget(b, i // 3, i % 3)
        self.lay.addLayout(pad)
        self.buttons("OK")
        self.edit.setFocus()

    def _key(self, k):
        if k == "⌫":
            self.edit.backspace()
        else:
            if self.edit.hasSelectedText():
                self.edit.clear()
            self.edit.insert(k)

    def value(self) -> float:
        try:
            return float(self.edit.text() or 0)
        except ValueError:
            return 0.0


class DiscountDialog(BaseDialog):
    def __init__(self, parent, title: str, base_paisa: int, presets=(5, 10, 15, 20)):
        super().__init__(parent, title, 360)
        self.base = base_paisa
        self.pct = QtyEdit(decimals=2)
        self.pct.setPlaceholderText("%")
        self.amt = MoneyEdit(0)
        self.amt.setText("")
        self.amt.setPlaceholderText("$")
        form = QFormLayout()
        form.addRow("Percent off", self.pct)
        form.addRow("…or dollars off", self.amt)
        self.lay.addWidget(label(f"Amount before discount: {rs(base_paisa)}", "muted"))
        self.lay.addLayout(form)
        row = QHBoxLayout()
        for p in presets:
            row.addWidget(button(f"{p:g}%", slot=lambda _=False, p=p: self.pct.setText(f"{p:g}")))
        self.lay.addLayout(row)
        self.buttons("Apply")

    def discount_paisa(self) -> int:
        if self.amt.text():
            return min(self.amt.paisa(), self.base)
        return min(round(self.base * self.pct.value() / 100), self.base)

    def percent(self) -> float:
        if self.amt.text() and self.base:
            return self.amt.paisa() / self.base * 100
        return self.pct.value()


# ---------------------------------------------------------------- tender
class TenderDialog(BaseDialog):
    """Take payment: one or more methods, quick cash amounts, change calculation."""
    def __init__(self, parent, s, total: int, has_customer: bool, can_charge: bool = True):
        super().__init__(parent, "Take payment", 640)
        self.s = s
        self.total = total
        self.has_customer = has_customer
        self.tenders: list[dict] = []
        self.methods = s.db.dicts("SELECT * FROM payment_methods WHERE active = 1 AND on_register = 1 ORDER BY sort")
        if not can_charge:
            self.methods = [m for m in self.methods if m["code"] != "CHARGE"]
        self.due_lbl = label("", "bigTotal")
        self.lay.addWidget(label("Amount due", "muted"))
        self.lay.addWidget(self.due_lbl)
        grid = QGridLayout()
        self.group = QButtonGroup(self)
        for i, m in enumerate(self.methods):
            b = QPushButton(m["name"])
            b.setObjectName("tender")
            b.setCheckable(True)
            b.setProperty("code", m["code"])
            self.group.addButton(b, i)
            grid.addWidget(b, i // 4, i % 4)
        self.group.buttonClicked.connect(self._method_changed)
        self.lay.addLayout(grid)
        self.amount = MoneyEdit(0)
        self.amount.setObjectName("amount")
        self.ref = QLineEdit()
        self.ref.setPlaceholderText("Card: last 4 digits (optional)  ·  Check: check number")
        self.auth = QLineEdit()
        self.auth.setPlaceholderText("Approval code from the card machine (optional)")
        form = QFormLayout()
        form.addRow("Amount received", self.amount)
        form.addRow("Card last 4 / check #", self.ref)
        form.addRow("Approval code", self.auth)
        self.lay.addLayout(form)
        self.quick = QHBoxLayout()
        self.lay.addLayout(self.quick)
        self.list = QListWidget()
        self.list.setMaximumHeight(90)
        self.lay.addWidget(self.list)
        self.status = label("", "sectionTitle")
        self.lay.addWidget(self.status)
        row = QHBoxLayout()
        row.addWidget(button("Add this payment (split)", slot=self.add_payment))
        row.addWidget(button("Remove last", slot=self.remove_last))
        row.addStretch(1)
        self.lay.addLayout(row)
        self.ok = self.buttons("Complete sale")
        self.amount.returnPressed.connect(self._enter)
        if self.methods:
            self.group.button(0).setChecked(True)
            self._method_changed(self.group.button(0))
        self._refresh()

    def _method(self):
        b = self.group.checkedButton()
        return next((m for m in self.methods if m["code"] == b.property("code")), None) if b else None

    def paid(self) -> int:
        return sum(t["tendered"] for t in self.tenders)

    def remaining(self) -> int:
        return self.total - self.paid()

    def _method_changed(self, _b):
        m = self._method()
        self.ref.setEnabled(bool(m and (m["requires_ref"] or m["is_card"])))
        self.auth.setEnabled(bool(m and m["is_card"]))
        self.amount.set_paisa(max(self.remaining(), 0))
        self.amount.selectAll()
        self.amount.setFocus()
        self._build_quick(m)

    def _build_quick(self, m):
        while self.quick.count():
            w = self.quick.takeAt(0).widget()
            if w:
                w.deleteLater()
        rem = max(self.remaining(), 0)
        if not m or not m["is_cash"] or rem <= 0:
            return
        options = [rem]
        for step in (100, 500, 1000, 2000, 5000, 10000):       # next $1, $5, $10, $20, $50, $100
            v = ((rem + step - 1) // step) * step
            if v > rem and v not in options:
                options.append(v)
        for v in options[:6]:
            self.quick.addWidget(button("Exact" if v == rem else rs(v), slot=lambda _=False, v=v: self._quick(v)))

    def _quick(self, v):
        self.amount.set_paisa(v)
        self.add_payment()

    def _enter(self):
        if self.remaining() > 0:
            self.add_payment()
        if self.remaining() <= 0:
            self._accept()

    def add_payment(self):
        m = self._method()
        amt = self.amount.paisa()
        if not m or amt <= 0:
            return
        if m["requires_customer"] and not self.has_customer:
            error(self, f"{m['name']} needs a customer. Close this and choose a customer first.")
            return
        if not m["is_cash"] and amt > self.remaining():
            error(self, "Only cash can be more than the amount due (to give change).")
            return
        ref = self.ref.text().strip()
        if m["requires_ref"] and not ref:
            error(self, f"Enter the {m['name'].lower()} number")
            return
        if m["is_card"] and ref and (not ref.isdigit() or len(ref) != 4):
            error(self, "For a card, enter only the LAST 4 digits (never the full card number)")
            return
        self.tenders.append({"method": m["code"], "name": m["name"], "tendered": amt, "ref": ref,
                             "auth": self.auth.text().strip()})
        self.ref.clear()
        self.auth.clear()
        self._refresh()
        self._method_changed(None)

    def remove_last(self):
        if self.tenders:
            self.tenders.pop()
            self._refresh()
            self._method_changed(None)

    def _refresh(self):
        self.list.clear()
        for t in self.tenders:
            self.list.addItem(f"{t['name']}: {rs(t['tendered'])}" + (f"  (#{t['ref']})" if t["ref"] else ""))
        rem = self.remaining()
        self.due_lbl.setText(rs(self.total))
        if rem > 0:
            self.status.setText(f"Still due: {rs(rem)}")
            self.status.setStyleSheet(f"color:{pal()['bad']}")
        else:
            self.status.setText(f"Change to give: {rs(-rem)}" if rem < 0 else "Paid in full")
            self.status.setStyleSheet(f"color:{pal()['good']}; font-size:16pt")

    def validate(self):
        if self.remaining() > 0:
            # allow pressing Complete with the typed amount not yet added
            self.add_payment()
        return self.remaining() <= 0

    def change(self) -> int:
        return max(-self.remaining(), 0)


# ---------------------------------------------------------------- customer
class CustomerDialog(BaseDialog):
    def __init__(self, parent, s, cart, user_id: int):
        super().__init__(parent, "Customer", 680)
        self.s = s
        self.cart = cart
        self.user_id = user_id
        self.customer_id = cart.customer_id
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, phone or email…")
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Name", "Phone", "Price level", "Owes", "Store credit"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.doubleClicked.connect(self._accept)
        self.lay.addWidget(self.search)
        self.lay.addWidget(self.table, 1)
        self.new_name = QLineEdit()
        self.new_phone = QLineEdit()
        form = QFormLayout()
        form.addRow("New customer name", self.new_name)
        form.addRow("Phone", self.new_phone)
        self.lay.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(button("Walk-in (no customer)", slot=self._walkin))
        row.addWidget(button("Save as new customer", slot=self._new))
        row.addStretch(1)
        self.lay.addLayout(row)
        self.buttons("Use selected")
        self.search.textChanged.connect(self._load)
        self._load()
        self.resize(720, 560)

    def _load(self):
        like = f"%{self.search.text().strip()}%"
        self.rows = self.s.db.dicts("SELECT * FROM customers WHERE active = 1 AND (name ILIKE ? OR COALESCE(phone,'') ILIKE ?"
                                    " OR COALESCE(email,'') ILIKE ?) ORDER BY name LIMIT 200", (like, like, like))
        levels = {int(r["code"]): r["name"] for r in self.s.db.all("SELECT code, name FROM lookups WHERE list_name = 'price_level'")}
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            vals = [r["name"], r["phone"] or "", levels.get(r["price_level"], str(r["price_level"])),
                    fmt_money(self.s.sales.customer_balance(r["id"])), fmt_money(self.s.sales.store_credit(r["id"]))]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j >= 3:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(i, j, it)
            if r["id"] == self.customer_id:
                self.table.selectRow(i)

    def _walkin(self):
        self.customer_id = None
        self.table.clearSelection()
        self.accept()

    def _new(self):
        name = self.new_name.text().strip() or self.search.text().strip()
        if not name:
            error(self, "Type the new customer's name first")
            return
        from ..core.lists import get_entity
        cid = self.s.lists.save(get_entity("customers"), {"name": name, "phone": self.new_phone.text().strip() or None},
                                self.user_id)
        self.customer_id = cid
        self.accept()

    def validate(self):
        row = self.table.currentRow()
        if row >= 0 and self.table.selectionModel().hasSelection():
            self.customer_id = self.rows[row]["id"]
        return True

    def apply(self):
        self.cart.customer_id = self.customer_id
        c = self.s.db.one("SELECT * FROM customers WHERE id = ?", (self.customer_id,)) if self.customer_id else None
        self.cart.customer_name = c["name"] if c else ""
        self.cart.price_level = int(c["price_level"]) if c else 1
        self.cart.tax_exempt = bool(c["tax_exempt"]) if c else False
        return c


# ---------------------------------------------------------------- returns
class ReturnDialog(BaseDialog):
    def __init__(self, parent, s, invoice_no: str = ""):
        super().__init__(parent, "Return or void a receipt", 760)
        self.s = s
        self.sale = None
        self.bill = QLineEdit(invoice_no)
        self.bill.setPlaceholderText("Receipt number (printed at the top of the receipt)")
        recent = s.db.all("SELECT receipt_no FROM sales WHERE doc_type = 'INV' AND is_active = 1 ORDER BY id DESC LIMIT 30")
        self.recent = QComboBox()
        self.recent.addItem("Recent receipts…")
        for r in recent:
            self.recent.addItem(r["receipt_no"])
        self.recent.activated.connect(lambda i: (self.bill.setText(self.recent.currentText()), self._load()) if i else None)
        self.lay.addLayout(hbox(label("Receipt #"), self.bill, button("Find", slot=self._load), self.recent))
        self.info = label("", "muted")
        self.lay.addWidget(self.info)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Item", "Sold", "Returned", "Return qty", "Back to stock", "Amount"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.lay.addWidget(self.table, 1)
        self.reason = QComboBox()
        self.reason.setEditable(True)
        for r in s.db.all("SELECT name FROM lookups WHERE list_name = 'return_reason' AND active = 1 ORDER BY sort"):
            self.reason.addItem(r["name"])
        self.method = RefCombo([(m["code"], m["name"]) for m in s.db.all(
            "SELECT code, name FROM payment_methods WHERE active = 1 ORDER BY sort")])
        self.cancel_all = QCheckBox("Void the whole receipt (refunds each payment the way it was paid)")
        form = QFormLayout()
        form.addRow("Reason", self.reason)
        form.addRow("Refund as", self.method)
        self.lay.addLayout(form)
        self.lay.addWidget(self.cancel_all)
        self.cancel_all.toggled.connect(self._toggle_all)
        self.buttons("Make the return")
        self.bill.returnPressed.connect(self._load)
        self.resize(800, 560)
        if invoice_no:
            self._load()

    def _load(self):
        self.sale = self.s.sales.find_sale(self.bill.text())
        if self.sale is None or self.sale["doc_type"] != "INV":
            self.info.setText("Receipt not found")
            self.table.setRowCount(0)
            return
        if not self.sale["is_active"]:
            self.info.setText("This receipt has already been voided")
            self.table.setRowCount(0)
            return
        from .widgets.grid import us_datetime
        self.info.setText(f"#{self.sale['receipt_no']} · {us_datetime(self.sale['created_at'])} · "
                          f"{self.sale['customer_name'] or 'Walk-in'} · Total {rs(self.sale['total'])}")
        first = self.s.db.val("SELECT method_code FROM sale_tenders WHERE sale_id = ? ORDER BY amount DESC LIMIT 1",
                              (self.sale["id"],))
        if first:
            self.method.set_value(first)
        self.lines = self.s.sales.returnable(self.sale["id"])
        self.table.setRowCount(len(self.lines))
        self.qty_edits = []
        self.restock = []
        for i, ln in enumerate(self.lines):
            self.table.setItem(i, 0, QTableWidgetItem(ln["description"]))
            self.table.setItem(i, 1, QTableWidgetItem(fmt_qty(ln["qty"])))
            self.table.setItem(i, 2, QTableWidgetItem(fmt_qty(ln["returned"])))
            q = QtyEdit()
            q.setPlaceholderText(f"max {fmt_qty(ln['returnable'])}")
            q.setEnabled(ln["returnable"] > 0)
            self.table.setCellWidget(i, 3, q)
            cb = QCheckBox()
            cb.setChecked(True)
            self.table.setCellWidget(i, 4, cb)
            self.table.setItem(i, 5, QTableWidgetItem(fmt_money(ln["total"])))
            self.qty_edits.append(q)
            self.restock.append(cb)

    def _toggle_all(self, on):
        for q, ln in zip(getattr(self, "qty_edits", []), getattr(self, "lines", [])):
            q.setText(f"{ln['returnable']:g}" if on and ln["returnable"] > 0 else "")
            q.setEnabled(not on and ln["returnable"] > 0)

    def validate(self):
        if self.sale is None:
            error(self, "Find the receipt first")
            return False
        if not self.reason.currentText().strip():
            error(self, "Give a reason")
            return False
        return True

    def selection(self) -> list[dict]:
        out = []
        for q, cb, ln in zip(self.qty_edits, self.restock, self.lines):
            if q.value() > 0:
                out.append({"line_id": ln["id"], "qty": q.value(), "restock": cb.isChecked()})
        return out


# ---------------------------------------------------------------- pick lists
class PickDialog(BaseDialog):
    """Choose one row from a small table."""
    def __init__(self, parent, title: str, headers: list[str], rows: list[list], payloads: list, ok_text="Open"):
        super().__init__(parent, title, 620)
        self.payloads = payloads
        self.table = QTableWidget(len(rows), len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i, r in enumerate(rows):
            for j, v in enumerate(r):
                self.table.setItem(i, j, QTableWidgetItem(str(v)))
        if rows:
            self.table.selectRow(0)
        self.table.doubleClicked.connect(self._accept)
        self.lay.addWidget(self.table, 1)
        self.buttons(ok_text)
        self.resize(660, 420)

    def validate(self):
        return self.table.currentRow() >= 0

    def choice(self):
        r = self.table.currentRow()
        return self.payloads[r] if r >= 0 else None


class CashMoveDialog(BaseDialog):
    def __init__(self, parent, s, kind: str):
        titles = {"payout": "Pay out cash from the drawer", "drop": "Move cash to the bank or safe",
                  "paid_in": "Put cash into the drawer"}
        super().__init__(parent, titles[kind], 420)
        self.kind = kind
        self.amount = MoneyEdit(0)
        self.amount.setObjectName("amount")
        self.reason = QComboBox()
        self.note = QLineEdit()
        form = QFormLayout()
        form.addRow("Amount ($)", self.amount)
        if kind == "payout":
            for r in s.db.all("SELECT code, name, value FROM lookups WHERE list_name = 'payout_reason' AND active = 1 ORDER BY sort"):
                self.reason.addItem(r["name"], r["value"] or "6900")
            form.addRow("Reason", self.reason)
        elif kind == "drop":
            for r in s.db.all("SELECT code, name FROM accounts WHERE subtype IN ('bank','cash') AND code <> '1000'"
                              " AND active = 1 ORDER BY subtype, code"):
                self.reason.addItem(r["name"], r["code"])
            form.addRow("To", self.reason)
        else:
            self.reason.addItem("From the safe", "1010")
            self.reason.addItem("Owner's money", "3000")
            form.addRow("From", self.reason)
        form.addRow("Note", self.note)
        self.lay.addLayout(form)
        self.buttons("Save")

    def validate(self):
        if self.amount.paisa() <= 0:
            error(self, "Enter an amount")
            return False
        return True
