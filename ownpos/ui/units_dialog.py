"""Item List > Packs & cases: the packs and cases of one item (6-pack = 6 can, case = 24 can), each with an
optional own price and barcode, and the unit Receiving starts with. Every pack and case is sold out of the
item's ONE stock count: selling a 6-pack takes 6 cans off the same on-hand number straight away."""
from __future__ import annotations

from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QLineEdit, QTableWidget

from ..core.money import fmt_money
from ..core.units import UnitError, save_units, units_for
from .dialogs import BaseDialog
from .widgets.common import MoneyEdit, QtyEdit, RefCombo, button, error, hbox, label


class ItemUnitsDialog(BaseDialog):
    def __init__(self, parent, win, item_id: int):
        super().__init__(parent, "Packs & cases", 640)
        self.win = win
        self.s = win.s
        self.item = self.s.db.one("SELECT * FROM items WHERE id = ?", (item_id,))
        base = self.item["unit"]
        self.lay.addWidget(label(f"{self.item['name']}: stock is kept in {base}", "sectionTitle"))
        self.lay.addWidget(label(
            f"Add the packs and cases this item is sold or bought in, and how many {base} each one holds. Example: "
            f"case = 24 means one case holds 24 {base}. Leave 'Own price' empty to charge the number of {base} x the "
            f"{base} price; fill it to give the pack its own price. Give the pack's own UPC (printed on the carton) "
            "so scanning it rings up the pack. There is only one stock count: on hand is always in "
            f"{base}, and a case sold or received changes it by 24 {base} at once.", "muted", wrap=True))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Unit", f"How many {base} in one", "Own price ($, optional)",
                                              "Barcode (optional)"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.Stretch)
        self.lay.addWidget(self.table, 1)
        self.lay.addLayout(hbox(button("Add pack / case", slot=lambda: self.add_row()),
                                button("Remove", slot=self.remove_row), None))
        self.buy = RefCombo([])
        self.lay.addLayout(hbox(label("Receiving starts in"), self.buy, None))
        for u in units_for(self.s.db, self.item, selling=True)[1:]:
            self.add_row(u.code, u.factor, u.price, u.barcode)
        if self.table.rowCount() == 0:
            self.add_row()
        self._refresh_buy()
        self.buy.set_value(self.item["purchase_unit"] or base)
        self.buttons("Save packs & cases")
        self.resize(720, 460)

    def _unit_choices(self):
        return [(r["code"], f"{r['code']} · {r['name']}") for r in self.s.db.all(
            "SELECT code, name FROM lookups WHERE list_name = 'unit' AND active = 1 ORDER BY sort, code")
            if r["code"].lower() != self.item["unit"].lower()]

    def add_row(self, unit=None, factor=None, price=0, barcode=""):
        from .typenew import make_creator
        r = self.table.rowCount()
        self.table.insertRow(r)
        combo = RefCombo(self._unit_choices(), blank="Choose…", on_new=make_creator(self, self.win, "unit"))
        if unit:
            combo.set_value(unit)
        combo.currentIndexChanged.connect(lambda *_: self._refresh_buy())
        self.table.setCellWidget(r, 0, combo)
        self.table.setCellWidget(r, 1, QtyEdit(factor or 0))
        self.table.setCellWidget(r, 2, MoneyEdit(price or 0))
        self.table.setCellWidget(r, 3, QLineEdit(barcode or ""))

    def remove_row(self):
        r = self.table.currentRow()
        if r < 0:
            r = self.table.rowCount() - 1
        if r >= 0:
            self.table.removeRow(r)
            self._refresh_buy()

    def rows(self) -> list[dict]:
        out = []
        for r in range(self.table.rowCount()):
            unit = self.table.cellWidget(r, 0).value()
            if not unit:
                continue
            out.append({"unit": unit, "factor": self.table.cellWidget(r, 1).value(),
                        "price": self.table.cellWidget(r, 2).cents(), "barcode": self.table.cellWidget(r, 3).text()})
        return out

    def _refresh_buy(self):
        current = self.buy.value() if self.buy.count() else None
        base = self.item["unit"]
        choices = [(base, base)] + [(r["unit"], r["unit"]) for r in self.rows()]
        self.buy.set_choices(choices)
        self.buy.set_value(current if current in [c[0] for c in choices] else base)

    def validate(self) -> bool:
        try:
            save_units(self.s.db, self.item["id"], self.rows(), self.buy.value())
        except UnitError as exc:
            error(self, str(exc))
            return False
        from ..core.auth import audit
        audit(self.s.db, self.win.user.id, "item_units", "item", self.item["id"],
              {r["unit"]: r["factor"] for r in self.rows()})
        return True

    @staticmethod
    def summary(db, item) -> str:
        units = units_for(db, item, selling=True)[1:]
        return ", ".join(f"{u.code} = {u.factor:g} {item['unit']}" + (f" (${fmt_money(u.price)})" if u.price else "")
                         for u in units)
