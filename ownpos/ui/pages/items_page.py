"""Item List (QuickBooks POS style): one row per item, edit cells like a spreadsheet, packs & cases, bulk
price changes with preview and history, shelf tags, and on-hand counts you can correct right in the grid.

Every item has ONE stock count. Receiving, sales, returns, packs and cases all change that same number
inside the same saved transaction, so On hand is right the moment the change is saved (the list re-reads
it whenever the page is opened, and Refresh re-reads it at any time)."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QDialog, QDoubleSpinBox, QFormLayout, QHeaderView, QLineEdit, QSpinBox,
                               QTableWidget, QTableWidgetItem)

from ...core.money import fmt_money, fmt_qty
from ...core.pricing import MODES, PRICE_FIELDS, ROUNDING
from ..dialogs import BaseDialog
from ..widgets.common import RefCombo, button, confirm, error, guarded, hbox, info, label, require
from ..widgets.grid import Col, DataGrid
from .lists_page import EntityPage

COST_COLUMNS = ("cost_avg", "last_cost")


class PriceChangeDialog(BaseDialog):
    """Change many prices at once: choose items, how to change, preview, apply."""
    def __init__(self, parent, s, selected_ids: list[int]):
        super().__init__(parent, "Change prices", 820)
        self.s = s
        self.changes: list[dict] = []
        self.scope = RefCombo([("selected", f"Selected items ({len(selected_ids)})")] if selected_ids else [])
        for d in s.db.all("SELECT id, name FROM departments WHERE active = 1 ORDER BY sort, name"):
            self.scope.addItem(f"All items in department {d['name']}", f"dept:{d['id']}")
        for v in s.db.all("SELECT id, name FROM vendors WHERE active = 1 ORDER BY name"):
            self.scope.addItem(f"All items from vendor {v['name']}", f"vendor:{v['id']}")
        self.scope.addItem("All active items", "all")
        self.selected_ids = selected_ids
        self.field = RefCombo(list(PRICE_FIELDS.items()))
        self.mode = RefCombo(list(MODES.items()))
        self.value = QDoubleSpinBox()
        self.value.setRange(-100000, 1000000)
        self.value.setDecimals(2)
        self.value.setValue(5)
        self.rounding = RefCombo(list(ROUNDING.items()))
        self.rounding.set_value(1)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("e.g. distributor price increase, holiday sale")
        form = QFormLayout()
        form.addRow("Which items", self.scope)
        form.addRow("Which price", self.field)
        form.addRow("How", self.mode)
        form.addRow("Value", self.value)
        form.addRow("Round to", self.rounding)
        form.addRow("Reason", self.reason)
        self.lay.addLayout(form)
        self.lay.addLayout(hbox(button("Preview", slot=self.preview), None))
        self.grid = DataGrid([Col("item_no", "Item #", width=80), Col("name", "Item", width=220),
                              Col("size", "Size", width=70),
                              Col("cost", "Avg cost", "money", total=False), Col("old", "Now", "money", total=False),
                              Col("new", "New", "money", total=False), Col("change", "Change", "money", total=False),
                              Col("warn", "Check", width=150)], key="price_change", toolbar=False, filters=False)
        self.grid.setMinimumHeight(280)
        self.lay.addWidget(self.grid, 1)
        self.note = label("", "muted", wrap=True)
        self.lay.addWidget(self.note)
        self.buttons("Apply price changes")
        self.mode.currentIndexChanged.connect(self._mode_hint)
        self._mode_hint()
        for w in (self.scope, self.field, self.mode, self.rounding):
            w.currentIndexChanged.connect(self._stale)
        self.value.valueChanged.connect(self._stale)
        self.resize(880, 680)

    def _stale(self, *_):
        """Any change to the inputs makes the old preview invalid."""
        if self.changes:
            self.changes = []
            self.grid.set_rows([])
            self.note.setText("Settings changed: press Preview again.")

    def _mode_hint(self):
        m = self.mode.value()
        self.value.setSuffix({"pct": " %", "amount": " $", "set": " $", "margin": " %"}[m])

    def _item_ids(self) -> list[int]:
        sc = self.scope.value()
        if sc == "selected":
            return list(self.selected_ids)
        if sc == "all":
            return [r[0] for r in self.s.db.all("SELECT id FROM items WHERE active = 1")]
        kind, ref = str(sc).split(":")
        col = "department_id" if kind == "dept" else "vendor_id"
        return [r[0] for r in self.s.db.all(f"SELECT id FROM items WHERE active = 1 AND {col} = ?", (int(ref),))]

    @guarded
    def preview(self):
        self.changes = self.s.pricing.preview(self._item_ids(), self.field.value(), self.mode.value(),
                                              self.value.value(), int(self.rounding.value()))
        rows = []
        below = 0
        for c in self.changes:
            r = dict(c)
            if c["below_cost"]:
                r["warn"] = "Below cost"
                r["_bg"] = "#FDE8E7"
                below += 1
            elif c["new"] == 0:
                r["warn"] = "Price would be zero"
                r["_bg"] = "#FEF3C7"
            rows.append(r)
        self.grid.set_rows(rows)
        moving = sum(1 for c in self.changes if c["new"] != c["old"])
        self.note.setText(f"{moving} of {len(self.changes)} prices will change." +
                          (f" {below} would be below average cost." if below else ""))

    def validate(self):
        if not self.changes:
            self.preview()
            if self.changes:
                info(self, "Here is the preview. Check it, then press Apply price changes again to save.")
            return False
        if any(c["new"] == 0 for c in self.changes if c["new"] != c["old"]):
            if not confirm(self, "Some new prices would be $0.00. Apply anyway?"):
                return False
        if any(c["below_cost"] for c in self.changes):
            return confirm(self, "Some new prices are below average cost. Apply anyway?")
        return True


class LabelDialog(BaseDialog):
    """Print shelf tags / barcode labels for the selected items."""
    def __init__(self, parent, win, items: list):
        super().__init__(parent, "Print shelf tags", 720)
        self.win = win
        self.s = win.s
        self.items = items
        from ..labels import DEFAULT_SIZE, SIZES
        st = self.s.db.settings()
        self.size = RefCombo([(k, k) for k in SIZES])
        self.size.set_value(st.get("label_size") if st.get("label_size") in SIZES else DEFAULT_SIZE)
        self.show_shop = QCheckBox("Store name")
        self.show_shop.setChecked(bool(st.get("label_show_shop", True)))
        self.show_price = QCheckBox("Price")
        self.show_price.setChecked(True)
        from ..printing import printer_names
        self.printer = RefCombo([(n, n) for n in printer_names()], blank="(default printer)")
        self.printer.set_value(st.get("label_printer", ""))
        form = QFormLayout()
        form.addRow("Label size", self.size)
        form.addRow("Show", hbox(self.show_shop, self.show_price, None))
        form.addRow("Label printer", self.printer)
        self.lay.addLayout(form)
        self.lay.addWidget(label("Copies per item. The barcode is the item's UPC (or its item # when it has none); "
                                 "the register reads it like the bottle's own barcode.", "muted", wrap=True))
        self.table = QTableWidget(len(items), 3)
        self.table.setHorizontalHeaderLabels(["Item", "Price", "Copies"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for c in (1, 2):
            self.table.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.copies = []
        for r, it in enumerate(items):
            self.table.setItem(r, 0, QTableWidgetItem(it["name"] + (f"  {it['size']}" if it["size"] else "")))
            self.table.setItem(r, 1, QTableWidgetItem(f"${fmt_money(it['price1'])}"))
            sp = QSpinBox()
            sp.setRange(0, 500)
            sp.setValue(1)
            self.table.setCellWidget(r, 2, sp)
            self.copies.append(sp)
        self.table.setMinimumHeight(min(80 + 34 * len(items), 360))
        self.lay.addWidget(self.table, 1)
        self.lay.addLayout(hbox(None, button("Preview", slot=self.preview), button("Print", "primary", self.do_print),
                                button("Close", slot=self.reject)))
        self.resize(760, 600)

    def _spec(self):
        from ..labels import SIZES, LabelSpec
        w, h = SIZES[self.size.value()]
        return LabelSpec(w, h, self.show_shop.isChecked(), self.show_price.isChecked())

    def _labels(self) -> list[dict]:
        from ..labels import label_for_item
        out = []
        for it, sp in zip(self.items, self.copies):
            if sp.value():
                out += [label_for_item(self.s.db, it)] * sp.value()
        if not out:
            raise ValueError("Set at least one copy")
        return out

    def _remember(self):
        db = self.s.db
        db.set_setting("label_size", self.size.value())
        db.set_setting("label_show_shop", self.show_shop.isChecked())
        db.set_setting("label_printer", self.printer.value() or "")

    @guarded
    def preview(self):
        from ..labels import preview_labels
        preview_labels(self, self._labels(), self._spec(), self.printer.value() or "")

    @guarded
    def do_print(self):
        from ..labels import print_labels
        from ..printing import printer_names
        labels = self._labels()
        if not printer_names():
            error(self, "No printer is installed on this computer. Use Preview to check the labels.")
            return
        print_labels(labels, self._spec(), self.printer.value() or "")
        self._remember()
        self.win.toast(f"Sent {len(labels)} label(s) to the printer")
        self.accept()


class PriceHistoryDialog(BaseDialog):
    def __init__(self, parent, s, item_id: int | None):
        super().__init__(parent, "Price history", 760)
        rows = s.pricing.history(item_id)
        fields = dict(PRICE_FIELDS)
        for r in rows:
            r["field"] = fields.get(r["field"], r["field"])
        g = DataGrid([Col("changed_at", "When", "datetime", width=140), Col("item_no", "Item #"),
                      Col("name", "Item", width=180), Col("field", "Price"), Col("old_price", "Was", "money"),
                      Col("new_price", "Now", "money"), Col("reason", "Reason", width=160),
                      Col("batch_no", "Change no."), Col("username", "By")],
                     rows, key="price_history", toolbar=True)
        g.setMinimumHeight(380)
        self.lay.addWidget(g, 1)
        self.buttons("Close", cancel=False)
        self.resize(900, 560)


class ItemsPage(EntityPage):
    """Inventory > Item List."""
    def __init__(self, win):
        super().__init__(win, "items", title="Item List")
        self.layout().insertWidget(1, label(
            "Every item has one on-hand count. Packs and cases sell out of that same count. Double-click a row "
            "for the full form; type in a cell to change it. On hand: type the counted quantity to correct it "
            "(posted as a count, logged).", "muted", wrap=True))

    def hidden_fields(self) -> set:
        return set() if self.win.user.can("costs") else set(COST_COLUMNS)

    @guarded
    def add(self):
        from .lists_page import EntityForm
        d = EntityForm(self, self.s.lists, self.form_entity(), win=self.win)
        d.widgets["item_no"][1].setText(self.s.lists.next_item_no())
        if d.exec() == QDialog.Accepted:
            self.s.lists.save(self.ent, d.values(), self.win.user.id)
            self.reload()
            self.after_change()

    def extra_buttons(self):
        out = [button("Refresh", slot=self.reload, tip="Read the list again (on-hand counts change as other "
                                                       "stations sell and receive)")]
        if self.win.user.can("items"):
            out.append(button("Copy as new item", slot=self.copy_item,
                              tip="Start a new item from the selected one (e.g. the same whiskey in another size)"))
            out.append(button("Packs && cases", slot=self.edit_units,
                              tip="6-pack, 12-pack, case of 24…: sold from the same stock count as the single item"))
            out.append(button("Change prices", slot=self.change_prices,
                              tip="Raise or lower many prices at once, with preview and history"))
        out.append(button("Item history", slot=self.item_history,
                          tip="Every sale, receiving, return, count and adjustment of the selected item"))
        out.append(button("Print shelf tags", slot=self.print_labels, tip="Price tags and barcode labels"))
        out.append(button("Price history", slot=self.price_history))
        return out

    @guarded
    def copy_item(self):
        row = self.grid.current_row()
        if row is None:
            error(self, "Click the item to copy first")
            return
        from .lists_page import EntityForm
        values = {k: v for k, v in row.items() if not k.startswith("_")}
        for k in ("id", "upc", "alt_lookup", "vendor_item_no"):
            values.pop(k, None)
        values["item_no"] = self.s.lists.next_item_no()
        values["name"] = f"{row['name']}"
        d = EntityForm(self, self.s.lists, self.form_entity(), values, "Copy as new item (give it its own UPC)",
                       win=self.win)
        if d.exec() == QDialog.Accepted:
            self.s.lists.save(self.ent, d.values(), self.win.user.id)
            self.reload()
            self.after_change()
            self.win.toast("New item saved. Stock starts at zero: receive it to add stock.")

    @guarded
    def item_history(self):
        row = self.grid.current_row()
        if row is None:
            error(self, "Click the item first")
            return
        from .history_page import ItemHistoryDialog
        ItemHistoryDialog(self, self.s, row["id"], self.win.user.can("costs")).exec()

    @guarded
    def edit_units(self):
        row = self.grid.current_row()
        if row is None:
            error(self, "Click the item first")
            return
        from ..units_dialog import ItemUnitsDialog
        if ItemUnitsDialog(self, self.win, row["id"]).exec():
            self.win.toast("Packs & cases saved.")
            if "register" in self.win.pages:
                self.win.pages["register"].refresh_suggestions()

    def columns(self):
        cols = super().columns()
        for c in cols:
            if c.key == "on_hand" and self.win.user.can("adjust"):
                c.editable = True
            if c.key == "cost_avg" and self.win.user.role == "owner":
                c.editable = True                  # owner only: a logged cost correction
        return cols

    def on_edit(self, row, col: Col, value):
        if col.key == "on_hand":
            return self._set_count(row, value)
        if col.key == "cost_avg":
            return self._set_cost(row, value)
        return super().on_edit(row, col, value)

    def _set_cost(self, row, value):
        """Owner: correct the average cost. The stock value and the accounts follow (document CC-…)."""
        from ...core.lists import get_entity as _ge
        field = next(f for f in _ge("items").fields if f.key == "cost_avg")
        new = self.s.lists.coerce(field, value)
        item = self.s.db.one("SELECT * FROM items WHERE id = ?", (row["id"],))
        if new is None or abs(float(new) - float(item["cost_avg"])) < 0.5:
            return item["cost_avg"]
        reason = ask_cost_reason(self, item, float(new))
        if not reason:
            return item["cost_avg"]
        self.s.stock.correct_cost(item["id"], float(new), reason, self.win.user)
        self.win.toast(f"{item['name']}: cost corrected to ${fmt_money(round(new))} per {item['unit']}.")
        return float(self.s.db.val("SELECT cost_avg FROM items WHERE id = ?", (item["id"],)))

    def _set_count(self, row, value):
        try:
            counted = float(str(value).replace(",", "") or 0)
        except ValueError:
            error(self, "Type the counted quantity as a number")
            return row["on_hand"]
        if counted < 0:
            error(self, "A count cannot be negative")
            return row["on_hand"]
        item = self.s.db.one("SELECT * FROM items WHERE id = ?", (row["id"],))
        if item["kind"] in ("service", "non_inventory"):
            error(self, "This item type does not keep stock")
            return row["on_hand"]
        old = float(item["on_hand"])           # read now: another station may have sold some since the list opened
        if abs(counted - old) < 1e-9:
            return old
        if not confirm(self, f"Change on hand of {item['name']} from {fmt_qty(old)} to {fmt_qty(counted)} "
                             f"{item['unit']}?\nThis posts a count correction (stock value and the accounts update, "
                             "and it is logged)."):
            return old
        self.s.stock.count({item["id"]: counted}, self.win.user.id, note="Corrected in Item List")
        new = float(self.s.db.val("SELECT on_hand FROM items WHERE id = ?", (item["id"],), 0))
        self.win.toast(f"{item['name']}: on hand {fmt_qty(old)} → {fmt_qty(new)} {item['unit']}")
        return new

    @guarded
    def change_prices(self):
        approver = require(self.win, "items", "Changing prices")
        if approver is None:
            return
        ids = [r["id"] for r in self.grid.selected_rows()]
        d = PriceChangeDialog(self, self.s, ids)
        if d.exec() == QDialog.Accepted:
            n = self.s.pricing.apply(d.changes, d.field.value(), approver.id, d.reason.text().strip())
            info(self, f"{n} price(s) changed. The old prices are kept in Price history.")
            self.reload()
            self.after_change()

    @guarded
    def print_labels(self):
        rows = self.grid.selected_rows()
        if not rows:
            error(self, "Select the items to label first (Ctrl+click or Shift+click for several).")
            return
        items = self.s.db.all("SELECT * FROM items WHERE id = ANY(?) ORDER BY name", ([r["id"] for r in rows],))
        LabelDialog(self, self.win, items).exec()

    @guarded
    def price_history(self):
        row = self.grid.current_row()
        PriceHistoryDialog(self, self.s, row["id"] if row and len(self.grid.selected_rows()) == 1 else None).exec()


def ask_cost_reason(parent, item, new_cost: float) -> str | None:
    """Explain what a cost correction does and ask why. Returns the reason, or None to cancel."""
    from ..widgets.common import ask_text
    value = round(float(item["on_hand"]) * new_cost) - round(float(item["on_hand"]) * float(item["cost_avg"]))
    text = (f"Change the average cost of {item['name']} from ${fmt_money(round(item['cost_avg']))} to "
            f"${fmt_money(round(new_cost))} per {item['unit']}?\n\nThe {fmt_qty(item['on_hand'])} {item['unit']} on "
            f"hand change in value by ${fmt_money(value)}. This is posted in the accounts (5100 Inventory cost "
            "corrections) and logged.\n\nWhy is the cost being corrected? (for example: vendor invoice had the "
            "wrong cost)")
    reason = ask_text(parent, "Correct stock cost", text, "")
    return (reason or "").strip() or None
