"""Make a Sale: the register screen."""
from __future__ import annotations

from PySide6.QtCore import QEvent, QStringListModel, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QCompleter, QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QMenu, QPushButton, QScrollArea, QSizePolicy, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ...core.db import CommitUnknown, ServerUnavailable, now_dt
from ...core.money import fmt_money, fmt_qty, round_cents as round_paisa
from ...core.sales import Cart, SaleError
from ..dialogs import CashMoveDialog, CustomerDialog, DiscountDialog, NumberDialog, PickDialog, ReturnDialog, TenderDialog
from ..theme import pal
from ..widgets.common import (button, confirm, error, guarded, hbox, info, label, panel, require, rs, server_down, vbox,
                              ask_text)


class Tile(QPushButton):
    """Quick item button: each line is elided to the tile width so long names never clip."""
    def __init__(self, lines: list[str]):
        super().__init__("\n".join(lines))
        self.lines = lines
        self.setToolTip("\n".join(lines))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        fm = self.fontMetrics()
        room = max(self.width() - 14, 20)
        self.setText("\n".join(fm.elidedText(ln, Qt.ElideRight, room) for ln in self.lines))


class RegisterPage(QWidget):
    sale_done = Signal(int)

    def __init__(self, win):
        super().__init__()
        self.setObjectName("page")
        self.win = win
        self.s = win.s
        self.cart = Cart()
        self.last_sale_id = None
        self._suggest_stamp = None
        self._build()
        self._shortcuts()
        self.refresh_tiles()
        self.refresh_cart()

    # ---- layout ------------------------------------------------------
    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(12)

        # left: scan + cart
        left = QVBoxLayout()
        left.setSpacing(8)
        self.scan = QLineEdit()
        self.scan.setObjectName("scan")
        self.scan.setPlaceholderText("Scan a UPC, or type an item name, item # or size")
        self.scan.setToolTip("Tip: type 3* then scan (or 3*1001) to add 3 at once")
        self.scan.returnPressed.connect(self.on_scan)
        # suggestions while typing: matches anywhere in the name, size, item number or UPC
        self._suggest = {}                       # text shown in the list -> item id
        self.completer = QCompleter(self)
        self.completer.setModel(QStringListModel(self.completer))
        self.completer.setCaseSensitivity(Qt.CaseInsensitive)
        self.completer.setFilterMode(Qt.MatchContains)
        self.completer.setMaxVisibleItems(12)
        self.completer.popup().setStyleSheet("QListView { font-size: 12pt; } QListView::item { padding: 6px 4px; }")
        self.completer.activated.connect(self._suggestion_chosen)
        self.scan.setCompleter(self.completer)
        self.cust_btn = button("Customer: Walk-in  (F4)", slot=self.pick_customer)
        self.cust_btn.setMinimumWidth(180)
        left.addLayout(hbox(self.scan, self.cust_btn))
        self.banner = label("", "muted")
        left.addWidget(self.banner)
        self.table = QTableWidget(0, 6)
        self.table.setObjectName("cart")
        self.table.setHorizontalHeaderLabels(["#", "Item", "Qty", "Price", "Discount", "Amount"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for c, w in ((0, 36), (2, 90), (3, 100), (4, 90), (5, 110)):
            self.table.setColumnWidth(c, w)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.doubleClicked.connect(lambda *_: self.edit_qty())
        self.table.cellClicked.connect(self._cell_clicked)
        left.addWidget(self.table, 1)
        # two rows of line buttons, so the sale screen also fits laptops with Windows scaling at 125-150 %
        line_btns = QGridLayout()
        line_btns.setSpacing(6)
        for i, b in enumerate((button("Qty (F2)", slot=self.edit_qty), button("− 1", slot=lambda: self.bump(-1)),
                               button("+ 1", slot=lambda: self.bump(1)), button("Pack / case (Ctrl+U)", slot=self.choose_unit),
                               button("Price", slot=self.edit_price), button("Line discount (F3)", slot=self.line_discount),
                               button("Receipt discount", slot=self.bill_discount),
                               button("Remove (Del)", "danger", self.remove_line))):
            line_btns.addWidget(b, i // 4, i % 4)
        left.addLayout(line_btns)
        # employee discount buttons (Lists > Discount buttons): 10% / 15% / 20% by default, cashier's discretion
        self.disc_row = QHBoxLayout()
        self.disc_row.setSpacing(6)
        left.addLayout(self.disc_row)
        self._discount_buttons()
        root.addLayout(left, 58)

        # right: actions, tiles, totals
        right = QVBoxLayout()
        right.setSpacing(8)
        acts = QGridLayout()
        acts.setSpacing(6)
        items = [("Hold (F5)", self.hold), ("Recall (F6)", self.recall), ("Return / void (F7)", self.do_return),
                 ("Price check (F8)", self.price_check), ("Reprint", self.reprint), ("Clear sale", self.clear_sale),
                 ("More…", None)]
        for i, (t, fn) in enumerate(items):
            b = button(t, slot=fn) if fn else button(t)
            if fn is None:
                self.more_btn = b
                m = QMenu(b)
                m.addAction("Open drawer (no sale)", self.no_sale)
                m.addAction("Pay out cash", lambda: self.cash_move("payout"))
                m.addAction("Cash drop to bank / safe", lambda: self.cash_move("drop"))
                m.addAction("Paid in", lambda: self.cash_move("paid_in"))
                m.addSeparator()
                m.addAction("Payment on house charge account", self.payment_on_account)
                m.addSeparator()
                m.addAction("Arrange quick buttons…", self.arrange_quick)
                b.setMenu(m)
            acts.addWidget(b, i // 3, i % 3)
        right.addLayout(acts)

        self.cat_bar = QHBoxLayout()
        self.cat_bar.setSpacing(4)
        cat_wrap = QWidget()
        cat_wrap.setLayout(self.cat_bar)
        cat_scroll = QScrollArea()
        cat_scroll.setWidget(cat_wrap)
        cat_scroll.setWidgetResizable(True)
        cat_scroll.setFixedHeight(48)
        cat_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        cat_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        right.addWidget(cat_scroll)
        self.tile_grid = QGridLayout()
        self.tile_grid.setSpacing(6)
        tiles = QWidget()
        tiles.setLayout(self.tile_grid)
        tile_scroll = QScrollArea()
        tile_scroll.setWidget(tiles)
        tile_scroll.setWidgetResizable(True)
        tile_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tile_grid.setAlignment(Qt.AlignTop)
        right.addWidget(tile_scroll, 1)

        tot = panel()
        tl = QGridLayout(tot)
        tl.setContentsMargins(14, 10, 14, 10)
        tl.setVerticalSpacing(2)
        self.t_labels = {}
        rate = self.s.db.val("SELECT rate FROM tax_codes WHERE code = 'IN7'", default=7.0)
        for r, (key, text) in enumerate([("items", "Items"), ("subtotal", "Subtotal"), ("discount", "Discount"),
                                         ("nontaxable", "Not taxed"), ("tax", f"Sales tax {rate:g}%")]):
            tl.addWidget(label(text, "muted"), r, 0)
            v = label("0.00")
            v.setAlignment(Qt.AlignRight)
            tl.addWidget(v, r, 1)
            self.t_labels[key] = v
        self.total_lbl = label("$0.00", "bigTotal")
        self.total_lbl.setAlignment(Qt.AlignRight)
        tl.addWidget(label("Total", "sectionTitle"), 5, 0)
        tl.addWidget(self.total_lbl, 5, 1)
        right.addWidget(tot)
        pay_row = QHBoxLayout()
        self.exact_btn = button("Exact cash (F9)", slot=self.exact_cash)
        self.exact_btn.setMinimumHeight(56)
        self.pay_btn = button("PAY  (F12)", "pay", self.pay)
        self.pay_btn.setMinimumHeight(56)
        pay_row.addWidget(self.exact_btn, 1)
        pay_row.addWidget(self.pay_btn, 2)
        right.addLayout(pay_row)
        root.addLayout(right, 42)

    def _shortcuts(self):
        # F-keys work wherever the focus is on this screen (scan box, cart, a tile, or the menu
        # button that was just clicked). They are window shortcuts, so they are only live while
        # the register page is the one showing.
        for key, fn in (("F2", self.edit_qty), ("F3", self.line_discount), ("F4", self.pick_customer),
                        ("F5", self.hold), ("F6", self.recall), ("F7", self.do_return), ("F8", self.price_check),
                        ("F9", self.exact_cash), ("F10", self.pay), ("F12", self.pay), ("Delete", self.remove_line)):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(fn)
        for key, fn in (("Ctrl+U", self.choose_unit),):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(fn)
        for key, fn in (("+", lambda: self.bump(1)), ("-", lambda: self.bump(-1))):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WidgetWithChildrenShortcut)
            sc.activated.connect(fn)
        # the scan box keeps the focus, and a line edit claims Del for itself: when it is empty,
        # Del removes the selected cart line as the on-screen hint promises
        self.scan.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if obj is self.scan and ev.type() == QEvent.KeyPress and ev.key() == Qt.Key_Delete \
                and not self.scan.text() and ev.modifiers() == Qt.NoModifier:
            self.remove_line()
            return True
        return super().eventFilter(obj, ev)

    def showEvent(self, ev):
        super().showEvent(ev)
        self.refresh_suggestions()
        QTimer.singleShot(0, self.scan.setFocus)

    def refresh_suggestions(self):
        """Fill the search box's drop-down list from the current items. Runs when this screen opens and the
        item list has changed (on any station), so new items and new prices appear without restarting."""
        from ...core.units import Unit, unit_price
        stamp = tuple(self.s.db.one("SELECT COUNT(*), MAX(COALESCE(updated_at, created_at)),"
                                    " (SELECT COUNT(*) FROM item_units) FROM items"))
        if stamp == self._suggest_stamp:
            return
        self._suggest_stamp = stamp
        self._suggest = {}
        units: dict = {}
        for u in self.s.db.all("SELECT * FROM item_units ORDER BY item_id, sort, id"):
            units.setdefault(u["item_id"], []).append(u)
        for it in self.s.db.all("SELECT * FROM items WHERE active = 1 ORDER BY name"):
            size = f" {it['size']}" if it["size"] else ""
            text = f"{it['name']}{size}  ·  ${fmt_money(it['price1'])}  ·  #{it['item_no']}"
            if it["upc"]:
                text += f"  ·  {it['upc']}"
            self._suggest[text] = (it["id"], None)
            # each pack / case is its own line in the list: "Light Lager 12OZ can · case (= 24 can) · $23.99"
            for r in units.get(it["id"], []):
                u = Unit(r["unit"], float(r["factor"]), int(r["price"] or 0), r["barcode"] or "")
                utext = (f"{it['name']}{size}  ·  {u.label(it['unit'])}  ·  ${fmt_money(unit_price(it, u))}"
                         f"  ·  #{it['item_no']}")
                if u.barcode:
                    utext += f"  ·  {u.barcode}"
                self._suggest[utext] = (it["id"], u.code)
        self.completer.model().setStringList(list(self._suggest))

    def _suggestion_chosen(self, text):
        """A line picked from the list (Enter or a click). The list puts its text back in the box: clear it after."""
        QTimer.singleShot(0, self.scan.clear)
        picked = self._suggest.get(text)
        if picked:
            item = self.s.db.one("SELECT * FROM items WHERE id = ?", (picked[0],))
            self.add_item(item, unit=picked[1])

    # ---- tiles -------------------------------------------------------
    def refresh_tiles(self):
        while self.cat_bar.count():
            w = self.cat_bar.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.cats = self.s.db.dicts("SELECT * FROM departments WHERE active = 1 AND show_on_register = 1 ORDER BY sort, name")
        self.cat_buttons = []
        fav = button("★ All quick items", slot=lambda: self.show_category(None))
        fav.setCheckable(True)
        self.cat_bar.addWidget(fav)
        self.cat_buttons.append((None, fav))
        for c in self.cats:
            b = button(c["name"], slot=lambda _=False, cid=c["id"]: self.show_category(cid))
            b.setCheckable(True)
            if c["color"]:
                b.setStyleSheet(f"QPushButton {{ border-left: 5px solid {c['color']}; }}")
            self.cat_bar.addWidget(b)
            self.cat_buttons.append((c["id"], b))
        self.cat_bar.addStretch(1)
        self.show_category(None)

    def show_category(self, cat_id):
        for cid, b in self.cat_buttons:
            b.setChecked(cid == cat_id)
        while self.tile_grid.count():
            w = self.tile_grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        if cat_id is None:
            rows = self.s.db.dicts("SELECT i.*, c.color FROM items i LEFT JOIN departments c ON c.id = i.department_id"
                                   " WHERE i.active = 1 AND i.on_register = 1 ORDER BY i.sort, c.sort, i.name LIMIT 200")
        else:
            rows = self.s.db.dicts("SELECT i.*, c.color FROM items i LEFT JOIN departments c ON c.id = i.department_id"
                                   " WHERE i.active = 1 AND i.department_id = ? AND i.on_register = 1"
                                   " ORDER BY i.sort, i.name LIMIT 200", (cat_id,))
        cols = 3
        for n, it in enumerate(rows):
            lines = [it["name"]] + ([it["size"]] if it["size"] else [])
            b = Tile(lines + [rs(it["price1"])])
            b.setObjectName("tile")
            b.setMinimumHeight(74)
            b.setMinimumWidth(60)
            b.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)
            c = pal()
            color = it.get("tile_color") or it["color"] or c["field_border"]
            b.setStyleSheet(f"QPushButton#tile {{ border: 1px solid {c['tile_border']}; border-top: 4px solid {color};"
                            f" background: {c['panel']}; }} QPushButton#tile:hover {{ background: {c['tile_hover']}; }}")
            b.clicked.connect(lambda _=False, iid=it["id"]: self.add_item_id(iid))
            self.tile_grid.addWidget(b, n // cols, n % cols)
        if not rows:
            self.tile_grid.addWidget(label("No quick buttons here yet. Use More… > Arrange quick buttons, or tick "
                                           "'Quick button' on items in the Item List (good for ice, bags, cups).",
                                           "muted", wrap=True), 0, 0)

    @guarded
    def arrange_quick(self):
        approver = require(self.win, "items", "Changing the quick buttons")
        if approver is None:
            return
        from ..quick_layout import QuickLayoutDialog
        if QuickLayoutDialog(self, self.s, approver.id).exec():
            self.refresh_tiles()
            self.refresh_suggestions()

    # ---- adding items ------------------------------------------------
    @guarded
    def on_scan(self):
        text = self.scan.text().strip()
        self.scan.clear()
        self.completer.popup().hide()            # close the suggestion list once something is entered
        if not text:
            if self.cart.lines:
                self.pay()
            return
        if text in self._suggest:                # a line from the list: _suggestion_chosen adds it
            return
        qty = 1.0
        explicit = "*" in text
        if explicit:
            q, _, text = text.partition("*")
            try:
                qty = float(q)
            except ValueError:
                error(self, "Use quantity*code, e.g. 3*1001")
                return
            if qty <= 0:
                error(self, "The quantity must be more than zero")
                return
        if explicit and not text:                # "3*" typed: the next scan adds 3
            self._next_qty = qty
            self.banner.setText(f"Next item x {fmt_qty(qty)}")
            return
        if getattr(self, "_next_qty", None):
            qty, explicit = self._next_qty, True
            self._next_qty = None
        found = self.s.sales.find_item(text)    # UPC, pack/case barcode, alternate lookup, item #, name
        if found is not None:
            item, unit = found
            self.add_item(item, qty if explicit else None, unit=unit)
            return
        matches = self.s.sales.search_items(text)
        if len(matches) == 1:
            self.add_item(matches[0], qty if explicit else None)
            return
        if matches:
            d = PickDialog(self, f"Items matching '{text}'", ["Item", "Size", "Price", "On hand", "Item #"],
                           [[m["name"], m["size"] or "", fmt_money(m["price1"]), fmt_qty(m["on_hand"]), m["item_no"]]
                            for m in matches], [m["id"] for m in matches], "Add")
            if d.exec() != QDialog.Accepted:
                return
            self.add_item(self.s.inventory.item(d.choice()), qty if explicit else None)
            return
        error(self, f"No item found for '{text}'. Check the Item List, or add the item there.")

    def add_item_id(self, item_id):
        item = self.s.db.one("SELECT * FROM items WHERE id = ?", (item_id,))
        self.add_item(item)

    @guarded
    def add_item(self, item, qty=None, unit=None):
        if not self.win.can_sell:
            error(self, self.win.cannot_sell_reason())
            return
        if not self.win.ensure_shift():
            return
        if not self._hours_ok(item):
            return
        level = self.cart.price_level or 1
        from ...core.units import get_unit, unit_price
        u = get_unit(self.s.db, item, unit)
        unit = u.code
        price = unit_price(item, u, level)
        qty = qty or 1
        # the same item again goes onto the existing line (unless that line has a discount or another price)
        for i, ln in enumerate(self.cart.lines):
            if ln.item_id == item["id"] and ln.discount == 0 and ln.price == price and ln.unit == unit:
                ln.qty = round(ln.qty + qty, 3)
                self.refresh_cart(select=i)
                self._stock_hint(item, ln.base_qty)
                return
        line = self.s.sales.make_line(item, qty, level, unit)
        cust = self._customer()
        if cust and cust["discount_pct"]:
            line.discount = round_paisa(round_paisa(line.qty * line.price) * cust["discount_pct"] / 100)
        self.cart.lines.append(line)
        self.refresh_cart(select=len(self.cart.lines) - 1)
        self._stock_hint(item, line.base_qty)

    def _stock_hint(self, item, qty):
        if item["kind"] == "inventory" and item["on_hand"] - qty < 0:
            self.banner.setText(f"Note: on hand shows {fmt_qty(item['on_hand'])} {item['unit']} of {item['name']}. "
                                "Receive the vendor's invoice (or count it) so stock stays right.")
        else:
            self.banner.setText("")

    def _hours_ok(self, item) -> bool:
        """Sale hours: a warning the cashier can pass (once per sale), never a block."""
        if self.cart.hours_override:
            return True
        from ...core.hours import check_sale_time
        msg = check_sale_time(self.s.db, now_dt(), item["department_id"])
        if not msg:
            return True
        if confirm(self, msg + "\n\nContinue with this sale?", "Outside alcohol sale hours"):
            self.cart.hours_override = now_dt().strftime("%Y-%m-%d %H:%M")
            return True
        return False

    def _discount_buttons(self):
        while self.disc_row.count():
            w = self.disc_row.takeAt(0).widget()
            if w:
                w.deleteLater()
        for r in self.s.db.all("SELECT code, name, value FROM lookups WHERE list_name = 'discount_button' AND active = 1"
                               " ORDER BY sort"):
            try:
                pct = float(str(r["value"]).replace("%", ""))
            except (TypeError, ValueError):
                continue
            b = button(r["name"], slot=lambda _=False, p=pct, n=r["name"]: self.quick_discount(p, n))
            b.setToolTip(f"{pct:g}% off the whole receipt, recorded as '{r['name']}'")
            self.disc_row.addWidget(b)
        clear = button("No discount", slot=lambda: self.quick_discount(0, ""))
        self.disc_row.addWidget(clear)
        self.disc_row.addStretch(1)

    @guarded
    def quick_discount(self, pct: float, name: str):
        if not self.cart.lines:
            return
        if pct and not self._discount_ok(pct):
            return
        self.s.sales.apply_sale_discount(self.cart, pct, name)
        self.refresh_cart()

    @guarded
    def price_check(self):
        text = ask_text(self, "Price check", "Scan or type the item:")
        if not text:
            return
        found = self.s.sales.find_item(text)
        if found is None:
            matches = self.s.sales.search_items(text, 1)
            found = (matches[0], None) if matches else None
        if found is None:
            error(self, f"No item found for '{text}'")
            return
        item, unit = found
        from ...core.units import unit_price, units_for
        lines = [f"{item['name']} {item['size'] or ''}".strip(), f"Item #{item['item_no']}   UPC {item['upc'] or '-'}",
                 f"On hand: {fmt_qty(item['on_hand'])} {item['unit']}"]
        for u in units_for(self.s.db, item, selling=True):
            lines.append(f"{u.label(item['unit'])}: {rs(unit_price(item, u, self.cart.price_level or 1))}")
        info(self, "\n".join(lines), "Price check")

    def _customer(self):
        if not self.cart.customer_id:
            return None
        return self.s.db.one("SELECT * FROM customers WHERE id = ?", (self.cart.customer_id,))

    # ---- cart table --------------------------------------------------
    def refresh_cart(self, select: int | None = None):
        if self.cart.discount_pct:              # keep a receipt discount on lines added or changed after it
            self.s.sales.apply_sale_discount(self.cart, self.cart.discount_pct, self.cart.discount_reason)
        tot = self.s.sales.totals(self.cart)
        self.table.setRowCount(len(self.cart.lines))
        big = QFont()
        big.setPointSize(11)
        multi = self._multi_unit() if self.cart.lines else set()
        for i, (ln, lt) in enumerate(zip(self.cart.lines, tot["lines"])):
            name = ln.description + (f" {ln.size}" if ln.size else "")
            if ln.price != ln.list_price and ln.list_price:
                name += "   (price changed)"
            qty_text = f"{fmt_qty(ln.qty)} {ln.unit}"
            if ln.item_id and ln.item_id in multi:
                qty_text += "  ▾"                # click to choose another unit (box, pack…)
            vals = [str(i + 1), name, qty_text, fmt_money(ln.price),
                    fmt_money(lt.discount) if lt.discount else "", fmt_money(lt.net)]
            for j, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if j in (2, 3, 4, 5):
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if j == 5:
                    it.setFont(big)
                self.table.setItem(i, j, it)
        if select is not None and 0 <= select < len(self.cart.lines):
            self.table.selectRow(select)
            self.table.scrollToItem(self.table.item(select, 0))
        self.t_labels["items"].setText(fmt_qty(tot["items"]))
        self.t_labels["subtotal"].setText(fmt_money(tot["subtotal"]))
        self.t_labels["discount"].setText(("-" + fmt_money(tot["discount"])) if tot["discount"] else "0.00")
        self.t_labels["nontaxable"].setText(fmt_money(tot["nontaxable"]))
        self.t_labels["tax"].setText(fmt_money(tot["tax"]))
        self.total_lbl.setText(rs(tot["total"]))
        c = self._customer()
        who = c["name"] if c else "Walk-in"
        if c:
            owes = self.s.sales.customer_balance(c["id"])
            if owes:
                who += f"  (owes {fmt_money(owes)})"
        self.cust_btn.setText(f"Customer: {who}  (F4)")
        extra = []
        if self.cart.discount_reason and tot["discount"]:
            extra.append(f"{self.cart.discount_reason}: {rs(tot['discount'])} off")
        if self.cart.tax_exempt:
            extra.append("Tax-exempt customer")
        if self.cart.price_level and self.cart.price_level != 1:
            # the name comes from Lists > Price levels, so renaming a level there shows here too
            name = self.s.db.val("SELECT name FROM lookups WHERE list_name = 'price_level' AND code = ?",
                                 (str(self.cart.price_level),), f"Price level {self.cart.price_level}")
            extra.append(f"{name} prices")
        if extra and not self.banner.text():
            self.banner.setText("  ·  ".join(extra))
        self.pay_btn.setEnabled(bool(self.cart.lines))
        self.exact_btn.setEnabled(bool(self.cart.lines))
        self.win.show_customer_display(self.cart.lines[-1].description if self.cart.lines else "Welcome",
                                       f"Total {fmt_money(tot['total'])}")
        return tot

    def _multi_unit(self) -> set:
        """Items that have more than one selling unit (they show ▾ next to the quantity)."""
        return {r["item_id"] for r in self.s.db.all("SELECT DISTINCT item_id FROM item_units")}

    def _cell_clicked(self, row, col):
        if col == 2 and 0 <= row < len(self.cart.lines) and self.cart.lines[row].item_id in self._multi_unit():
            self.choose_unit()

    @guarded
    def choose_unit(self):
        """Sell the selected line in another unit of the same item: packet, box of 24, sack…"""
        i = self._sel()
        if i < 0:
            return
        ln = self.cart.lines[i]
        item = self.s.db.one("SELECT * FROM items WHERE id = ?", (ln.item_id,)) if ln.item_id else None
        if item is None:
            return
        from ...core.units import unit_price, units_for
        units = units_for(self.s.db, item, selling=True)
        if len(units) < 2:
            info(self, f"{item['name']} is sold only by the {item['unit']}. Add packs and cases in "
                       "Item List > Packs & cases.")
            return
        menu = QMenu(self)
        level = self.cart.price_level or 1
        for u in units:
            act = menu.addAction(f"{u.label(item['unit'])}   ·   ${fmt_money(unit_price(item, u, level))}")
            act.setCheckable(True)
            act.setChecked(u.code == ln.unit)
            act.triggered.connect(lambda _=False, code=u.code: self._set_unit(i, code))
        rect = self.table.visualItemRect(self.table.item(i, 2)) if self.table.item(i, 2) else None
        pos = self.table.viewport().mapToGlobal(rect.bottomLeft()) if rect else self.mapToGlobal(self.rect().center())
        menu.exec(pos)

    def _set_unit(self, i, code):
        ln = self.cart.lines[i]
        self.s.sales.set_line_unit(ln, code, self.cart.price_level or 1)
        cust = self._customer()
        if cust and cust["discount_pct"]:
            ln.discount = round_paisa(round_paisa(ln.qty * ln.price) * cust["discount_pct"] / 100)
        self.refresh_cart(select=i)

    def _sel(self):
        i = self.table.currentRow()
        if i < 0 and self.cart.lines:
            i = len(self.cart.lines) - 1
        return i

    @guarded
    def edit_qty(self):
        i = self._sel()
        if i < 0:
            return
        ln = self.cart.lines[i]
        d = NumberDialog(self, "Quantity", f"{ln.description} ({ln.unit})", f"{ln.qty:g}")
        if d.exec() == QDialog.Accepted and d.value() > 0:
            ln.qty = round(d.value(), 3)
            self.refresh_cart(select=i)

    def bump(self, delta):
        i = self._sel()
        if i < 0:
            return
        ln = self.cart.lines[i]
        if ln.qty + delta <= 0:
            self.remove_line()
            return
        ln.qty = round(ln.qty + delta, 3)
        self.refresh_cart(select=i)

    @guarded
    def edit_price(self):
        i = self._sel()
        if i < 0:
            return
        if require(self.win, "price_override", "Changing a price") is None:
            return
        ln = self.cart.lines[i]
        d = NumberDialog(self, "Price", f"New price for {ln.description} ($)", f"{ln.price / 100:.2f}", money=True)
        if d.exec() == QDialog.Accepted:
            ln.price = round(d.value() * 100)
            from ...core.auth import audit
            audit(self.s.db, self.win.user.id, "price_override", "item", ln.item_id, {"from": ln.list_price, "to": ln.price})
            self.refresh_cart(select=i)

    @guarded
    def line_discount(self):
        i = self._sel()
        if i < 0:
            return
        ln = self.cart.lines[i]
        base = round_paisa(ln.qty * ln.price)
        if self.cart.discount_pct:
            error(self, f"This sale has a receipt discount ({self.cart.discount_reason}). Press 'No discount' "
                        "first to give a discount on one line instead.")
            return
        d = DiscountDialog(self, f"Discount on {ln.description}", base)
        if d.exec() != QDialog.Accepted:
            return
        if not self._discount_ok(d.percent()):
            return
        ln.discount = d.discount_paisa()
        self.refresh_cart(select=i)

    @guarded
    def bill_discount(self):
        if not self.cart.lines:
            return
        base = sum(round_paisa(ln.qty * ln.price) for ln in self.cart.lines)
        d = DiscountDialog(self, "Discount on the whole receipt", base)
        if d.exec() != QDialog.Accepted:
            return
        if not self._discount_ok(d.percent()):
            return
        self.s.sales.apply_sale_discount(self.cart, d.percent(), f"Discount {d.percent():.0f}%")
        self.refresh_cart()

    def _discount_ok(self, pct: float) -> bool:
        limit = float(self.s.db.get_setting("max_discount_pct_cashier", 20) or 0)
        if pct <= limit and self.win.user.can("discount"):
            return True
        return require(self.win, "discount_any", f"A {pct:.0f}% discount") is not None

    def remove_line(self):
        i = self._sel()
        if i < 0:
            return
        ln = self.cart.lines.pop(i)
        from ...core.auth import audit
        audit(self.s.db, self.win.user.id, "void_line", "item", ln.item_id, {"description": ln.description, "qty": ln.qty})
        self.refresh_cart(select=min(i, len(self.cart.lines) - 1))

    def clear_sale(self):
        if self.cart.lines and not confirm(self, "Clear this sale?"):
            return
        for ln in self.cart.lines:
            from ...core.auth import audit
            audit(self.s.db, self.win.user.id, "void_line", "item", ln.item_id, {"description": ln.description, "qty": ln.qty})
        self.cart = Cart()
        self.banner.setText("")
        self.refresh_cart()

    # ---- customer / holds ---------------------------------------------
    @guarded
    def pick_customer(self):
        d = CustomerDialog(self, self.s, self.cart, self.win.user.id)
        if d.exec() == QDialog.Accepted:
            old_level = self.cart.price_level
            c = d.apply()
            if self.cart.price_level != old_level:
                self.s.sales.reprice(self.cart)
            if c and c["discount_pct"]:
                for ln in self.cart.lines:
                    ln.discount = round_paisa(round_paisa(ln.qty * ln.price) * c["discount_pct"] / 100)
            self.banner.setText("")
            self.refresh_cart()

    @guarded
    def hold(self):
        if not self.cart.lines:
            return
        lbl = ask_text(self, "Hold sale", "Label for this held sale (optional):", self.cart.customer_name)
        if lbl is None:
            return
        self.s.sales.hold(self.cart, self.win.user.id, lbl)
        self.cart = Cart()
        self.refresh_cart()
        self.win.toast("Sale held. Press F6 to recall it.")

    @guarded
    def recall(self):
        holds = self.s.sales.holds()
        if not holds:
            info(self, "No held sales.")
            return
        if self.cart.lines:
            error(self, "Finish or hold the current sale first.")
            return
        rows, ids = [], []
        for h in holds:
            c = Cart.from_json(h["data_json"])
            rows.append([h["label"] or "(no label)", h["created_at"][11:16], h["username"] or "",
                         fmt_money(self.s.sales.totals(c)["total"])])
            ids.append(h["id"])
        d = PickDialog(self, "Recall a held sale", ["Label", "Time", "By", "Total"], rows, ids, "Recall")
        if d.exec() == QDialog.Accepted:
            self.cart = self.s.sales.recall(d.choice())
            self.refresh_cart()

    # ---- payment -----------------------------------------------------
    @guarded
    def exact_cash(self):
        if not self.cart.lines:
            return
        total = self.s.sales.totals(self.cart)["total"]
        self._finish([{"method": "CASH", "tendered": total}], 0)

    @guarded
    def pay(self):
        if not self.cart.lines:
            return
        if not self.win.ensure_shift():
            return
        total = self.s.sales.totals(self.cart)["total"]
        d = TenderDialog(self, self.s, total, bool(self.cart.customer_id), self.win.user.can("house_charge"))
        if d.exec() != QDialog.Accepted:
            return
        self._finish(d.tenders, d.change())

    def _finish(self, tenders, change):
        """Save the sale. If the network breaks while saving, reconnect and look the sale up by its key before
        trying again, so it is never saved (or charged) twice; the cart stays on screen until it is saved."""
        if not self.win.can_sell:
            error(self, self.win.cannot_sell_reason())
            return
        while True:
            try:
                shift = self.s.shifts.current()
                sid = self.s.sales.finalize(self.cart, tenders, self.win.user.id, shift["id"] if shift else None)
                break
            except SaleError as exc:
                error(self, str(exc))
                return
            except ServerUnavailable as exc:
                if not server_down(self, exc):
                    self.win.toast("Sale NOT saved yet: it is still on the screen. Press PAY again when the "
                                   "server is back.", 15000)
                    return
                done = self.s.sales.find_by_key(self.cart.sale_key)
                if done is not None:            # it had been saved before the connection broke
                    sid = done["id"]
                    break
                if isinstance(exc, CommitUnknown):
                    continue
        self.last_sale_id = sid
        findings = self.s.agents.on_sale(sid)
        if any(t["method"] == "CASH" for t in tenders):
            self.win.open_drawer()
        self.win.print_receipt(sid)
        self.cart = Cart()
        self.banner.setText("")
        self.refresh_cart()
        inv = self.s.db.val("SELECT receipt_no FROM sales WHERE id = ?", (sid,))
        msg = f"Receipt #{inv} saved." + (f"  CHANGE: {rs(change)}" if change else "")
        warn = [f for f in findings if f.severity != "info"]
        if warn:
            msg += f"   ⚠ {warn[0].title}"
        self.win.toast(msg, 9000)
        if change:
            self.banner.setText(f"Change to give: {rs(change)}")
            self.banner.setStyleSheet(f"color:{pal()['good']}; font-size:15pt; font-weight:600")
            QTimer.singleShot(8000, lambda: (self.banner.setText(""), self.banner.setStyleSheet("")))
        self.sale_done.emit(sid)
        self.scan.setFocus()

    # ---- other register actions --------------------------------------
    @guarded
    def do_return(self, receipt_no=""):
        receipt_no = receipt_no if isinstance(receipt_no, str) else ""     # a button click passes True/False
        approver = require(self.win, "return", "A return")
        if approver is None:
            return
        d = ReturnDialog(self, self.s, receipt_no)
        if d.exec() != QDialog.Accepted:
            return
        shift = self.s.shifts.current()
        sid = shift["id"] if shift else None
        if d.cancel_all.isChecked():
            if not approver.can("void_receipt"):
                approver = require(self.win, "void_receipt", "Voiding a whole receipt")
                if approver is None:
                    return
            cn = self.s.sales.cancel_bill(d.sale["id"], d.reason.currentText(), approver.id, sid, d.method.value())
        else:
            sel = d.selection()
            if not sel:
                error(self, "Enter a return quantity on at least one line")
                return
            cn = self.s.sales.credit_note(d.sale["id"], sel, d.method.value(), approver.id, sid, d.reason.currentText())
        self.s.agents.on_sale(cn)
        method = d.method.value()
        if method == "CASH":
            self.win.open_drawer()
        self.win.print_receipt(cn)
        total = self.s.db.val("SELECT total FROM sales WHERE id = ?", (cn,))
        self.win.toast(f"Return saved. Refund {rs(total)}.", 9000)

    @guarded
    def reprint(self):
        rows = self.s.db.all("SELECT id, receipt_no, created_at, total, customer_name, print_count, station_id FROM sales"
                             " ORDER BY id DESC LIMIT 60")
        if not rows:
            return
        from ..widgets.grid import us_datetime
        d = PickDialog(self, "Reprint a receipt (prints as a copy)", ["Receipt #", "Time", "Station", "Customer", "Total",
                                                                      "Printed"],
                       [[r["receipt_no"], us_datetime(r["created_at"]), r["station_id"] or "", r["customer_name"] or "",
                         fmt_money(r["total"]), r["print_count"]] for r in rows], [r["id"] for r in rows], "Reprint")
        if d.exec() == QDialog.Accepted:
            self.win.print_receipt(d.choice(), force=True)

    @guarded
    def no_sale(self):
        approver = require(self.win, "no_sale", "Opening the drawer without a sale")
        if approver is None:
            return
        reason = ask_text(self, "No sale", "Why is the drawer being opened?", "Change for customer")
        if reason is None:
            return
        self.s.shifts.cash_move("no_sale", 0, approver.id, reason)
        self.win.open_drawer()

    @guarded
    def cash_move(self, kind):
        approver = require(self.win, "payout", "Moving cash in or out of the drawer")
        if approver is None:
            return
        d = CashMoveDialog(self, self.s, kind)
        if d.exec() != QDialog.Accepted:
            return
        self.s.shifts.cash_move(kind, d.amount.paisa(), approver.id, d.reason.currentText(), d.reason.currentData(),
                                d.note.text())
        self.win.open_drawer()
        self.win.toast(f"Recorded {rs(d.amount.paisa())}.")

    @guarded
    def payment_on_account(self):
        if not self.cart.customer_id:
            self.pick_customer()
        if not self.cart.customer_id:
            return
        cust = self._customer()
        owes = self.s.sales.customer_balance(cust["id"])
        d = NumberDialog(self, "Payment on account", f"{cust['name']} owes {rs(owes)}. Amount received ($):",
                         f"{owes / 100:.2f}", money=True)
        if d.exec() != QDialog.Accepted or d.value() <= 0:
            return
        methods = self.s.db.all("SELECT code, name FROM payment_methods WHERE active = 1 AND code NOT IN"
                                " ('CHARGE','STORECREDIT') ORDER BY sort")
        p = PickDialog(self, "Paid by", ["Method"], [[m["name"]] for m in methods], [m["code"] for m in methods], "OK")
        if p.exec() != QDialog.Accepted:
            return
        shift = self.s.shifts.current()
        ref = ""
        if p.choice() == "CHECK":
            ref = ask_text(self, "Check", "Check number:") or ""
            if not ref:
                return
        self.s.sales.receive_payment(cust["id"], round(d.value() * 100), p.choice(), self.win.user.id,
                                     shift["id"] if shift else None, ref=ref)
        if p.choice() == "CASH":
            self.win.open_drawer()
        self.win.toast(f"Payment of {rs(round(d.value() * 100))} recorded for {cust['name']}.")
        self.refresh_cart()
