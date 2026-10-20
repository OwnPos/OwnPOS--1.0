"""History lists like QuickBooks Point of Sale: Sales History (find any receipt, see its lines and payments,
view, reprint, return or void it), Purchasing History (receiving vouchers, vendor returns, purchase orders,
vendor payments), and the Item / Customer / Vendor history windows opened from their lists.

Everything here only READS; returns and voids go through the same checked steps as at the register."""
from __future__ import annotations

from datetime import date

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QLineEdit, QSplitter, QTabWidget, QVBoxLayout, QWidget

from ...core.db import today_str
from ...core.money import fmt_money, fmt_qty
from ..dialogs import BaseDialog
from ..widgets.common import DateEdit, RefCombo, button, error, guarded, hbox, info, label, page_header, usd
from ..widgets.grid import Col, DataGrid

MAX_ROWS = 10000
DATE_PRESETS = ["Today", "Yesterday", "This week", "Last week", "This month", "Last month", "This quarter",
                "Last quarter", "This year", "Last year", "Custom"]


class DateRange(QWidget):
    """Preset + From + To, the same presets as the Report Center (server's date)."""
    def __init__(self, s, preset="Today", on_change=None):
        super().__init__()
        self.s = s
        self.on_change = None          # set at the end: the owner page is not built yet while this one is
        self.preset = QComboBox()
        self.preset.addItems(DATE_PRESETS)
        self.d_from, self.d_to = DateEdit(), DateEdit()
        self._busy = False
        lay = hbox(self.preset, label("From"), self.d_from, label("To"), self.d_to)
        self.setLayout(lay)
        self.preset.currentTextChanged.connect(self._preset)
        for d in (self.d_from, self.d_to):
            d.changed.connect(self._typed)
        self.preset.setCurrentText(preset)
        self._preset(preset)
        self.on_change = on_change

    def _preset(self, name):
        if name == "Custom":
            return
        from .reports_page import preset_range
        ws = self.s.db.get_setting("week_starts", 6)
        a, b = preset_range(name, date.fromisoformat(today_str()), 6 if ws in (None, "") else int(ws))
        self._busy = True
        try:
            self.d_from.set_date(a)
            self.d_to.set_date(b)
        finally:
            self._busy = False
        if self.on_change:
            self.on_change()

    def _typed(self):
        if not self._busy and self.preset.currentText() != "Custom":
            self.preset.blockSignals(True)
            self.preset.setCurrentText("Custom")
            self.preset.blockSignals(False)

    def range(self) -> tuple[str, str]:
        return self.d_from.iso(), self.d_to.iso()


def _signed(row: dict, keys=("subtotal", "discount", "tax", "total")):
    """Return receipts (CN) are stored as positive amounts; history shows them as negative."""
    if row.get("doc_type") == "CN":
        for k in keys:
            if row.get(k) is not None:
                row[k] = -row[k]
    return row


# ======================================================================================== Sales History
class SalesHistoryPage(QWidget):
    """Point of Sale > Sales History."""
    def __init__(self, win):
        super().__init__()
        self.setObjectName("page")
        self.win = win
        self.s = win.s
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        u = win.user
        acts = [button("View receipt", slot=self.view_receipt), button("Reprint", slot=self.reprint)]
        if u.can("return") and u.can("sell"):
            acts.append(button("Return / void…", "primary", self.return_receipt))
        lay.addLayout(page_header("Sales History", "Every receipt and return. Click one to see its items and "
                                  "payments.", *acts))
        self.dates = DateRange(self.s, "Today", self.reload)
        self.kind = RefCombo([("all", "Sales and returns"), ("INV", "Sales only"), ("CN", "Returns and voids only")])
        self.station = RefCombo([], blank="All stations")
        self.cashier = RefCombo([], blank="All cashiers")
        self.customer = RefCombo([], blank="All customers")
        self.find = QLineEdit()
        self.find.setPlaceholderText("Receipt #, or an item name / UPC / item # on the receipt")
        self.find.returnPressed.connect(self.reload)
        for w in (self.kind, self.station, self.cashier, self.customer):
            w.currentIndexChanged.connect(lambda *_: self.reload())
        lay.addLayout(hbox(self.dates, None))
        lay.addLayout(hbox(self.kind, self.station, self.cashier, self.customer, self.find,
                           button("Find", slot=self.reload), None))
        self.note = label("", "muted")
        lay.addWidget(self.note)
        split = QSplitter(Qt.Vertical)
        self.grid = DataGrid([Col("receipt_no", "Receipt #", width=90), Col("kind", "Type", width=70),
                              Col("created_at", "Date / time", "datetime", width=150),
                              Col("station", "Station", width=90), Col("cashier", "Cashier", width=130),
                              Col("customer", "Customer", width=150), Col("items", "Items", "qty"),
                              Col("subtotal", "Subtotal", "money"), Col("discount", "Discount", "money"),
                              Col("tax", "Tax", "money"), Col("total", "Total", "money"),
                              Col("tenders", "Paid by", width=170), Col("status", "Status", width=150),
                              Col("print_count", "Prints", "int", width=55)],
                             key="sales_history", export_title="Sales history")
        self.grid.selection_changed.connect(self._detail_later)
        self.grid.activated.connect(lambda _r: self.view_receipt())
        split.addWidget(self.grid)
        lower = QWidget()
        ll = QVBoxLayout(lower)
        ll.setContentsMargins(0, 6, 0, 0)
        self.d_title = label("", "sectionTitle")
        ll.addWidget(self.d_title)
        self.lines = DataGrid([Col("line_no", "#", "int", width=40), Col("item_no", "Item #", width=80),
                               Col("description", "Item", width=260), Col("qty", "Qty", "qty"),
                               Col("unit", "Unit", width=60), Col("price", "Price", "money", total=False),
                               Col("discount", "Discount", "money"), Col("net", "Net", "money"),
                               Col("tax", "Tax", "money"), Col("total", "Total", "money"),
                               Col("returned", "Returned", "qty")],
                              key="sales_history_lines", toolbar=False, filters=False)
        ll.addWidget(self.lines, 1)
        self.d_pay = label("", "muted", wrap=True)
        ll.addWidget(self.d_pay)
        split.addWidget(lower)
        split.setSizes([520, 300])
        lay.addWidget(split, 1)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._detail)

    def on_show(self):
        db = self.s.db
        for combo, sql, blank in (
                (self.station, "SELECT id, name FROM stations ORDER BY id", "All stations"),
                (self.cashier, "SELECT id, full_name FROM users ORDER BY full_name", "All cashiers"),
                (self.customer, "SELECT id, name FROM customers ORDER BY name", "All customers")):
            v = combo.value()
            combo.blockSignals(True)
            combo.set_choices([(r[0], r[1]) for r in db.all(sql)], blank=blank)
            combo.set_value(v)
            combo.blockSignals(False)
        self.reload()

    def reload(self):
        a, b = self.dates.range()
        if a > b:
            self.note.setText("The From date is after the To date.")
            return
        where, args = ["s.sale_date BETWEEN ? AND ?"], [a, b]
        if self.kind.value() in ("INV", "CN"):
            where.append("s.doc_type = ?")
            args.append(self.kind.value())
        for combo, col in ((self.station, "s.station_id"), (self.cashier, "s.user_id"),
                           (self.customer, "s.customer_id")):
            if combo.value():
                where.append(f"{col} = ?")
                args.append(combo.value())
        text = self.find.text().strip()
        if text:
            from ...core.barcode import lookup_keys
            keys = lookup_keys(text)
            where.append("(s.receipt_no = ? OR EXISTS (SELECT 1 FROM sale_lines l LEFT JOIN items i ON i.id = l.item_id"
                         " WHERE l.sale_id = s.id AND (l.description ILIKE ? OR lower(i.item_no) = lower(?)"
                         " OR i.upc = ? OR i.upc_key = ANY(?))))")
            args += [text.lstrip("#"), f"%{text}%", text, text, keys or [""]]
        rows = self.s.db.dicts(
            "SELECT s.id, s.receipt_no, s.doc_type, s.created_at, s.station_id, COALESCE(st.name, '') AS station,"
            " u.full_name AS cashier, COALESCE(s.customer_name, c.name, '') AS customer, s.subtotal, s.discount,"
            " s.tax, s.total, s.reason, s.notes, s.print_count, s.ref_sale_id, r.receipt_no AS ref_receipt,"
            " (SELECT COALESCE(SUM(l.qty), 0) FROM sale_lines l WHERE l.sale_id = s.id) AS items,"
            " (SELECT string_agg(t.method_code || CASE WHEN t.ref IS NOT NULL AND t.ref <> '' THEN ' ' || t.ref"
            "  ELSE '' END, ', ' ORDER BY t.id) FROM sale_tenders t WHERE t.sale_id = s.id) AS tenders,"
            " (SELECT COUNT(*) FROM sales x WHERE x.ref_sale_id = s.id) AS returns"
            " FROM sales s LEFT JOIN users u ON u.id = s.user_id LEFT JOIN customers c ON c.id = s.customer_id"
            " LEFT JOIN stations st ON st.id = s.station_id LEFT JOIN sales r ON r.id = s.ref_sale_id"
            f" WHERE {' AND '.join(where)} ORDER BY s.id DESC LIMIT {MAX_ROWS + 1}", args)
        more = len(rows) > MAX_ROWS
        rows = rows[:MAX_ROWS]
        for r in rows:
            if r["doc_type"] == "CN":
                r["kind"] = "Void" if r["notes"] == "Void" else "Return"
                r["status"] = f"of {r['ref_receipt']}" + (f" · {r['reason']}" if r["reason"] else "")
                r["items"] = -float(r["items"] or 0)
                r["_bg"] = "#FEF3F2"
            else:
                r["kind"] = "Sale"
                r["status"] = f"{r['returns']} return(s)" if r["returns"] else ""
            _signed(r)
        self.grid.set_rows(rows)
        net = sum(r["total"] for r in rows)
        self.note.setText(f"{len(rows):,} receipt(s), net {usd(net)} (returns are negative)." +
                          (f" Only the newest {MAX_ROWS:,} are shown: narrow the dates or filters." if more else ""))
        self._detail()

    def _detail_later(self):
        self._timer.start(120)      # arrow keys move quickly through the list: load the detail once they stop

    def _detail(self):
        r = self.grid.current_row()
        if r is None:
            self.d_title.setText("")
            self.lines.set_rows([])
            self.d_pay.setText("")
            return
        sign = -1 if r["doc_type"] == "CN" else 1
        lines = self.s.db.dicts("SELECT l.*, i.item_no FROM sale_lines l LEFT JOIN items i ON i.id = l.item_id"
                                " WHERE l.sale_id = ? ORDER BY l.line_no", (r["id"],))
        returned = {}
        if r["doc_type"] == "INV":
            returned = {x["ref_line_id"]: float(x["q"]) for x in self.s.db.all(
                "SELECT l.ref_line_id, SUM(l.qty) q FROM sale_lines l JOIN sales s ON s.id = l.sale_id"
                " WHERE s.ref_sale_id = ? GROUP BY l.ref_line_id", (r["id"],))}
        for ln in lines:
            ln["returned"] = returned.get(ln["id"]) or None
            for k in ("qty", "discount", "net", "tax", "total"):
                ln[k] = sign * ln[k]
        self.lines.set_rows(lines)
        self.d_title.setText(f"{r['kind']} {r['receipt_no']} · {r['cashier'] or ''} · {r['station'] or ''}")
        pays = self.s.db.dicts("SELECT t.*, p.name FROM sale_tenders t LEFT JOIN payment_methods p"
                               " ON p.code = t.method_code WHERE t.sale_id = ? ORDER BY t.id", (r["id"],))
        parts = []
        for p in pays:
            t = f"{p['name'] or p['method_code']} {usd(sign * p['amount'])}"
            if p["change"]:
                t += f" (tendered {usd(p['tendered'])}, change {usd(p['change'])})"
            if p["ref"]:
                t += f" · #{p['ref']}"
            if p["auth_code"]:
                t += f" · auth {p['auth_code']}"
            parts.append(t)
        self.d_pay.setText("Payments: " + ("; ".join(parts) if parts else "none"))

    def _current(self):
        r = self.grid.current_row()
        if r is None:
            error(self, "Click a receipt first")
        return r

    @guarded
    def view_receipt(self):
        r = self._current()
        if r is None:
            return
        from ...core.receipt import receipt_html
        from .. import printing
        html = receipt_html(self.s.db, r["id"], copy_no=max(1, int(r["print_count"] or 0)))
        printing.preview_html(self, html, int(self.s.db.get_setting("receipt_width_mm", 80)),
                              f"Receipt {r['receipt_no']} (shown as a copy)")

    @guarded
    def reprint(self):
        r = self._current()
        if r is None:
            return
        self.win.print_receipt(r["id"], force=True)        # logged; prints "COPY"
        self.reload()

    @guarded
    def return_receipt(self):
        r = self._current()
        if r is None:
            return
        receipt = r["ref_receipt"] if r["doc_type"] == "CN" else r["receipt_no"]
        if not self.win._may_open("register"):
            error(self, "Returns are made at a register.")
            return
        self.win.go("register")
        reg = self.win.pages.get("register")
        if reg is not None:
            reg.do_return(receipt)


# ================================================================================= Purchasing History
class PurchasingHistoryPage(QWidget):
    """Inventory > Purchasing History: receiving vouchers and vendor returns, purchase orders, vendor payments."""
    def __init__(self, win):
        super().__init__()
        self.setObjectName("page")
        self.win = win
        self.s = win.s
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.addLayout(page_header("Purchasing History", "Receiving vouchers, vendor returns, purchase orders and "
                                  "vendor payments. Click a voucher to see its lines."))
        self.dates = DateRange(self.s, "This month", self.reload)
        self.vendor = RefCombo([], blank="All vendors")
        self.vendor.currentIndexChanged.connect(lambda *_: self.reload())
        self.find = QLineEdit()
        self.find.setPlaceholderText("Voucher #, vendor invoice #, or an item on it")
        self.find.returnPressed.connect(self.reload)
        lay.addLayout(hbox(self.dates, self.vendor, self.find, button("Find", slot=self.reload), None))
        self.tabs = QTabWidget()
        # vouchers
        vw = QWidget()
        vl = QVBoxLayout(vw)
        split = QSplitter(Qt.Vertical)
        self.vouchers = DataGrid([Col("doc_no", "Voucher #", width=100), Col("kind_text", "Type", width=110),
                                  Col("bill_date", "Invoice date", "date", width=100),
                                  Col("created_at", "Entered", "datetime", width=150),
                                  Col("vendor", "Vendor", width=180), Col("bill_no", "Vendor invoice #", width=120),
                                  Col("lines", "Lines", "int", width=55), Col("qty", "Qty", "qty"),
                                  Col("total", "Total", "money"), Col("paid", "Paid / on account", width=170),
                                  Col("due_date", "Due", "date", width=100), Col("po_no", "PO", width=90),
                                  Col("user", "By", width=110)], key="receiving_history",
                                 export_title="Receiving history")
        self.vouchers.selection_changed.connect(self._voucher_lines)
        split.addWidget(self.vouchers)
        self.v_lines = DataGrid([Col("item_no", "Item #", width=80), Col("name", "Item", width=240),
                                 Col("size", "Size", width=70), Col("purchase_qty", "Qty", "qty"),
                                 Col("purchase_unit", "Unit", width=60), Col("qty", "In item units", "qty"),
                                 Col("unit_cost", "Cost per item unit", "money", total=False),
                                 Col("line_net", "Line total", "money")], key="receiving_history_lines",
                                export_title="Voucher lines")
        split.addWidget(self.v_lines)
        split.setSizes([450, 260])
        vl.addWidget(split, 1)
        self.tabs.addTab(vw, "Receiving vouchers && vendor returns")
        # purchase orders
        pw = QWidget()
        pl = QVBoxLayout(pw)
        self.po_status = RefCombo([("all", "All statuses"), ("draft", "Draft"), ("ordered", "Ordered"),
                                   ("received", "Received"), ("cancelled", "Cancelled")])
        self.po_status.currentIndexChanged.connect(lambda *_: self.reload())
        pl.addLayout(hbox(label("Status"), self.po_status, None))
        psplit = QSplitter(Qt.Vertical)
        self.pos = DataGrid([Col("po_no", "PO #", width=90), Col("created_at", "Created", "datetime", width=150),
                             Col("vendor", "Vendor", width=180), Col("status", "Status", width=90),
                             Col("lines", "Lines", "int", width=55), Col("est", "Estimated cost", "money"),
                             Col("received_as", "Received as", width=110), Col("by", "Created by", width=120)],
                            key="po_history", export_title="Purchase orders")
        self.pos.selection_changed.connect(self._po_lines)
        psplit.addWidget(self.pos)
        self.p_lines = DataGrid([Col("item_no", "Item #", width=80), Col("name", "Item", width=240),
                                 Col("qty", "Qty", "qty"), Col("unit", "Unit", width=60),
                                 Col("est", "Expected cost", "money")], key="po_history_lines",
                                export_title="PO lines")
        psplit.addWidget(self.p_lines)
        pl.addWidget(psplit, 1)
        self.tabs.addTab(pw, "Purchase orders")
        # vendor payments
        self.payments = DataGrid([Col("pay_date", "Date", "date", width=100), Col("vendor", "Vendor", width=200),
                                  Col("amount", "Amount", "money"), Col("account", "Paid from", width=200),
                                  Col("ref", "Check / ref #", width=120), Col("user", "By", width=110),
                                  Col("notes", "Notes", width=160)], key="vendor_payment_history",
                                 export_title="Vendor payments")
        self.tabs.addTab(self.payments, "Vendor payments")
        self.tabs.currentChanged.connect(lambda *_: self.reload())
        lay.addWidget(self.tabs, 1)

    def on_show(self):
        v = self.vendor.value()
        self.vendor.blockSignals(True)
        self.vendor.set_choices([(r["id"], r["name"]) for r in self.s.db.all("SELECT id, name FROM vendors ORDER BY name")],
                                blank="All vendors")
        self.vendor.set_value(v)
        self.vendor.blockSignals(False)
        self.reload()

    def reload(self):
        a, b = self.dates.range()
        i = self.tabs.currentIndex()
        if i == 0:
            self._load_vouchers(a, b)
        elif i == 1:
            self._load_pos(a, b)
        else:
            self._load_payments(a, b)

    def _load_vouchers(self, a, b):
        where, args = ["p.bill_date BETWEEN ? AND ?"], [a, b]
        if self.vendor.value():
            where.append("p.vendor_id = ?")
            args.append(self.vendor.value())
        text = self.find.text().strip()
        if text:
            where.append("(p.doc_no ILIKE ? OR p.bill_no ILIKE ? OR EXISTS (SELECT 1 FROM purchase_lines pl JOIN items i"
                         " ON i.id = pl.item_id WHERE pl.purchase_id = p.id AND (i.name ILIKE ? OR i.upc = ?"
                         " OR lower(i.item_no) = lower(?))))")
            args += [f"%{text}%", f"%{text}%", f"%{text}%", text, text]
        rows = self.s.db.dicts(
            "SELECT p.*, COALESCE(v.name, p.vendor_name) AS vendor, u.username AS user, po.po_no, a.name AS acct,"
            " (SELECT COUNT(*) FROM purchase_lines pl WHERE pl.purchase_id = p.id) AS lines,"
            " (SELECT COALESCE(SUM(pl.purchase_qty), 0) FROM purchase_lines pl WHERE pl.purchase_id = p.id) AS qty"
            " FROM purchases p LEFT JOIN vendors v ON v.id = p.vendor_id LEFT JOIN users u ON u.id = p.user_id"
            " LEFT JOIN purchase_orders po ON po.id = p.po_id LEFT JOIN accounts a ON a.code = p.payment_account"
            f" WHERE {' AND '.join(where)} ORDER BY p.id DESC LIMIT {MAX_ROWS}", args)
        for r in rows:
            if r["kind"] == "vendor_return":
                r["kind_text"] = "Vendor return"
                r["total"] = -r["total"]
                r["qty"] = -float(r["qty"] or 0)
                r["paid"] = f"Refund to {r['acct']}" if r["payment_account"] else "Credit on account"
                r["_bg"] = "#FEF3F2"
            else:
                r["kind_text"] = "Receiving"
                r["paid"] = f"Paid from {r['acct']}" if r["payment_account"] else "On account"
        self.vouchers.set_rows(rows)
        self._voucher_lines()

    def _voucher_lines(self):
        r = self.vouchers.current_row()
        if r is None:
            self.v_lines.set_rows([])
            return
        rows = self.s.db.dicts("SELECT pl.*, i.item_no, i.name, i.size FROM purchase_lines pl JOIN items i"
                               " ON i.id = pl.item_id WHERE pl.purchase_id = ? ORDER BY pl.id", (r["id"],))
        sign = -1 if r["kind"] == "vendor_return" else 1
        for x in rows:
            x["unit_cost"] = round(x["unit_cost"])
            for k in ("purchase_qty", "qty", "line_net"):
                x[k] = sign * x[k]
        self.v_lines.set_rows(rows)
        self.v_lines.export_title = f"Voucher {r['doc_no']}"

    def _load_pos(self, a, b):
        where, args = ["substr(po.created_at, 1, 10) BETWEEN ? AND ?"], [a, b]
        if self.vendor.value():
            where.append("po.vendor_id = ?")
            args.append(self.vendor.value())
        if self.po_status.value() != "all":
            where.append("po.status = ?")
            args.append(self.po_status.value())
        rows = self.s.db.dicts(
            "SELECT po.*, v.name AS vendor, CASE WHEN po.created_by_agent = 1 THEN 'Reorder helper' ELSE u.username"
            " END AS by, (SELECT COUNT(*) FROM po_lines pl WHERE pl.po_id = po.id) AS lines,"
            " (SELECT CAST(ROUND(SUM(pl.qty * pl.est_cost * COALESCE(pl.unit_factor, 1))) AS BIGINT) FROM po_lines pl"
            "  WHERE pl.po_id = po.id) AS est,"
            " (SELECT string_agg(p.doc_no, ', ') FROM purchases p WHERE p.po_id = po.id) AS received_as"
            " FROM purchase_orders po LEFT JOIN vendors v ON v.id = po.vendor_id LEFT JOIN users u ON u.id = po.user_id"
            f" WHERE {' AND '.join(where)} ORDER BY po.id DESC LIMIT {MAX_ROWS}", args)
        self.pos.set_rows(rows)
        self._po_lines()

    def _po_lines(self):
        r = self.pos.current_row()
        if r is None:
            self.p_lines.set_rows([])
            return
        rows = self.s.db.dicts("SELECT pl.*, i.item_no, i.name, COALESCE(pl.unit, i.unit) AS unit,"
                               " CAST(ROUND(pl.qty * pl.est_cost * COALESCE(pl.unit_factor, 1)) AS BIGINT) AS est"
                               " FROM po_lines pl JOIN items i ON i.id = pl.item_id WHERE pl.po_id = ? ORDER BY pl.id",
                               (r["id"],))
        self.p_lines.set_rows(rows)

    def _load_payments(self, a, b):
        where, args = ["COALESCE(vp.pay_date, substr(vp.created_at, 1, 10)) BETWEEN ? AND ?"], [a, b]
        if self.vendor.value():
            where.append("vp.vendor_id = ?")
            args.append(self.vendor.value())
        rows = self.s.db.dicts(
            "SELECT COALESCE(vp.pay_date, substr(vp.created_at, 1, 10)) AS pay_date, v.name AS vendor, vp.amount,"
            " vp.account || ' ' || COALESCE(a.name, '') AS account, vp.ref, u.username AS user, vp.notes"
            " FROM vendor_payments vp JOIN vendors v ON v.id = vp.vendor_id LEFT JOIN accounts a ON a.code = vp.account"
            f" LEFT JOIN users u ON u.id = vp.user_id WHERE {' AND '.join(where)} ORDER BY vp.id DESC LIMIT {MAX_ROWS}",
            args)
        self.payments.set_rows(rows)


# ================================================================================ history windows
DOC_NAMES = {"INV": "Sale", "CN": "Return", "RV": "Received", "VR": "Returned to vendor", "WS": "Breakage / waste",
             "AD": "Adjustment", "PI": "Physical count", "OS": "Opening stock", "CC": "Cost correction"}


class ItemHistoryDialog(BaseDialog):
    """Item List > Item history: every movement of one item's stock, with the running on-hand."""
    def __init__(self, parent, s, item_id: int, show_costs: bool = True):
        item = s.db.one("SELECT * FROM items WHERE id = ?", (item_id,))
        super().__init__(parent, f"Item history: {item['name']}", 980)
        self.s = s
        self.item = item
        self.dates = DateRange(s, "This year", self.reload)
        self.lay.addWidget(label(f"#{item['item_no']} · {item['name']} {item['size'] or ''} · on hand now "
                                 f"{fmt_qty(item['on_hand'])} {item['unit']} · price ${fmt_money(item['price1'])}",
                                 "sectionTitle"))
        self.lay.addLayout(hbox(self.dates, None))
        cols = [Col("ts", "Date / time", "datetime", width=150), Col("what", "What", width=140),
                Col("doc_no", "Document", width=110), Col("who", "Customer / vendor / reason", width=200),
                Col("qty", "Qty in / out", "qty"), Col("balance", "On hand after", "qty", total=False),
                Col("unit_cost", "Unit cost", "money", total=False), Col("value", "Value", "money"),
                Col("user", "By", width=100)]
        if not show_costs:
            cols = [c for c in cols if c.key not in ("unit_cost", "value")]
        self.grid = DataGrid(cols, key="item_history", export_title=f"Item history {item['item_no']}")
        self.grid.show_totals = False
        self.lay.addWidget(self.grid, 1)
        self.buttons("Close", cancel=False)
        self.resize(1040, 640)
        self.reload()

    def reload(self):
        a, b = self.dates.range()
        rows = self.s.db.dicts(
            "SELECT * FROM (SELECT m.*, SUM(m.qty) OVER (ORDER BY m.ts, m.id) AS balance FROM stock_moves m"
            " WHERE m.item_id = ?) x WHERE substr(x.ts, 1, 10) BETWEEN ? AND ? ORDER BY x.ts DESC, x.id DESC",
            (self.item["id"], a, b))
        sales = {r["id"]: r for r in self.s.db.all(
            "SELECT id, receipt_no, COALESCE(customer_name, '') AS who FROM sales WHERE id = ANY(?)",
            ([r["doc_id"] for r in rows if r["doc_type"] in ("INV", "CN")] or [0],))}
        purch = {r["id"]: r for r in self.s.db.all(
            "SELECT p.id, p.doc_no, COALESCE(v.name, p.vendor_name, '') AS who FROM purchases p"
            " LEFT JOIN vendors v ON v.id = p.vendor_id WHERE p.id = ANY(?)",
            ([r["doc_id"] for r in rows if r["doc_type"] in ("RV", "VR")] or [0],))}
        adj = {r["id"]: r for r in self.s.db.all(
            "SELECT id, doc_no, COALESCE(reason, '') AS who FROM adjustments WHERE id = ANY(?)",
            ([r["doc_id"] for r in rows if r["doc_type"] not in ("INV", "CN", "RV", "VR")] or [0],))}
        users = {r["id"]: r["username"] for r in self.s.db.all("SELECT id, username FROM users")}
        for r in rows:
            src = sales if r["doc_type"] in ("INV", "CN") else purch if r["doc_type"] in ("RV", "VR") else adj
            d = src.get(r["doc_id"])
            r["doc_no"] = (d["receipt_no"] if r["doc_type"] in ("INV", "CN") else d["doc_no"]) if d else ""
            r["who"] = (d["who"] if d else "") or (r["note"] or "")
            r["what"] = DOC_NAMES.get(r["doc_type"], r["kind"])
            r["user"] = users.get(r["user_id"], "")
            r["unit_cost"] = round(r["unit_cost"] or 0)
        self.grid.set_rows(rows)


class PartyHistoryDialog(BaseDialog):
    """Customer or vendor history: their receipts / vouchers, and their account (house charge or payable)."""
    def __init__(self, parent, s, party_type: str, party_id: int):
        is_c = party_type == "customer"
        p = s.db.one(f"SELECT * FROM {'customers' if is_c else 'vendors'} WHERE id = ?", (party_id,))
        super().__init__(parent, f"{'Customer' if is_c else 'Vendor'} history: {p['name']}", 980)
        self.s = s
        self.is_c = is_c
        self.pid = party_id
        self.dates = DateRange(s, "This year", self.reload)
        self.balance = label("", "sectionTitle")
        self.lay.addWidget(self.balance)
        self.lay.addLayout(hbox(self.dates, None))
        tabs = QTabWidget()
        if is_c:
            self.docs = DataGrid([Col("receipt_no", "Receipt #", width=90), Col("kind", "Type", width=70),
                                  Col("created_at", "Date / time", "datetime", width=150),
                                  Col("total", "Total", "money"), Col("tenders", "Paid by", width=200)],
                                 key="customer_history_docs", export_title=f"Receipts {p['name']}")
        else:
            self.docs = DataGrid([Col("doc_no", "Voucher #", width=100), Col("kind", "Type", width=110),
                                  Col("bill_date", "Invoice date", "date", width=100),
                                  Col("bill_no", "Vendor invoice #", width=130), Col("total", "Total", "money")],
                                 key="vendor_history_docs", export_title=f"Vouchers {p['name']}")
        tabs.addTab(self.docs, "Receipts" if is_c else "Receiving && returns")
        self.acct = DataGrid([Col("entry_date", "Date", "date", width=100), Col("doc_no", "Document", width=110),
                              Col("memo", "Memo", width=300), Col("charge", "Charges" if is_c else "Bills", "money"),
                              Col("payment", "Payments / credits", "money"),
                              Col("running", "Balance", "money", total=False)],
                             key=f"{party_type}_history_acct", export_title=f"Account {p['name']}")
        tabs.addTab(self.acct, "House charge account" if is_c else "Account (what we owe)")
        self.lay.addWidget(tabs, 1)
        self.buttons("Close", cancel=False)
        self.resize(1040, 640)
        self.reload()

    def reload(self):
        a, b = self.dates.range()
        db = self.s.db
        if self.is_c:
            rows = db.dicts(
                "SELECT s.id, s.receipt_no, s.doc_type, s.created_at, s.total,"
                " (SELECT string_agg(t.method_code, ', ') FROM sale_tenders t WHERE t.sale_id = s.id) AS tenders"
                " FROM sales s WHERE s.customer_id = ? AND s.sale_date BETWEEN ? AND ? ORDER BY s.id DESC",
                (self.pid, a, b))
            for r in rows:
                r["kind"] = "Return" if r["doc_type"] == "CN" else "Sale"
                _signed(r, ("total",))
            account = "1200"
        else:
            rows = db.dicts("SELECT p.* FROM purchases p WHERE p.vendor_id = ? AND p.bill_date BETWEEN ? AND ?"
                            " ORDER BY p.id DESC", (self.pid, a, b))
            for r in rows:
                r["kind"] = "Vendor return" if r["kind"] == "vendor_return" else "Receiving"
                if r["kind"] == "Vendor return":
                    r["total"] = -r["total"]
            account = "2000"
        self.docs.set_rows(rows)
        ptype = "customer" if self.is_c else "vendor"
        lines = db.dicts(
            "SELECT j.entry_date, j.doc_no, j.memo, l.debit, l.credit, j.id FROM journal_lines l"
            " JOIN journal j ON j.id = l.journal_id WHERE l.party_type = ? AND l.party_id = ? AND l.account = ?"
            " ORDER BY j.entry_date, j.id", (ptype, self.pid, account))
        running, out = 0, []
        for ln in lines:
            charge, payment = (ln["debit"], ln["credit"]) if self.is_c else (ln["credit"], ln["debit"])
            running += charge - payment
            if a <= ln["entry_date"] <= b:
                out.append({"entry_date": ln["entry_date"], "doc_no": ln["doc_no"], "memo": ln["memo"],
                            "charge": charge or None, "payment": payment or None, "running": running})
        out.reverse()
        self.acct.set_rows(out)
        self.balance.setText((f"Owes the store: {usd(running)}" if self.is_c else f"We owe: {usd(running)}") +
                             "  (all time)")
