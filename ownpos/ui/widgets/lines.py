"""LinesTable: a small editable table for entering document lines (receiving, purchase orders, breakage,
counts).

A column of kind "unit" is a drop-down right after the item: once an item is chosen it lists that item's
units (can, 6pk = 6 can, case = 24 can...). values() gives the chosen unit's code under the column key and its
factor (item's own units in one) under key + "_factor".

With 20,000 items, every row's item picker shares ONE list model, so adding a row stays instant."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QStandardItem, QStandardItemModel
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QCompleter, QHeaderView, QLineEdit,
                               QTableWidget, QTableWidgetItem)

from ...core.money import fmt_money, fmt_qty
from .common import MoneyEdit, QtyEdit


def item_label(it) -> str:
    size = f" {it['size']}" if it.get("size") else ""
    upc = f"  [{it['upc']}]" if it.get("upc") else ""
    return f"{it['item_no']} · {it['name']}{size}{upc}"


def add_to_model(model: QStandardItemModel, by_id: dict, items: list[dict]) -> int:
    """Bring a shared item list up to date: new items are appended, changed ones updated in place.
    Returns how many were added."""
    added = 0
    for it in items:
        if it["id"] in by_id:
            by_id[it["id"]].update(it)
            continue
        by_id[it["id"]] = it
        row = QStandardItem(item_label(it))
        row.setData(it["id"], Qt.UserRole)
        model.appendRow(row)
        added += 1
    return added


def item_model(items: list[dict]) -> QStandardItemModel:
    """One shared list of items for every row's picker (type to search by number, name, size or UPC)."""
    m = QStandardItemModel()
    blank = QStandardItem("")
    blank.setData(None, Qt.UserRole)
    m.appendRow(blank)
    for it in items:
        row = QStandardItem(item_label(it))
        row.setData(it["id"], Qt.UserRole)
        m.appendRow(row)
    return m


class ItemCombo(QComboBox):
    """Type-to-search item picker."""
    def __init__(self, items: list[dict] | None = None, model: QStandardItemModel | None = None):
        super().__init__()
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.NoInsert)
        # Qt's default size policy measures EVERY entry to size the box: with 20,000 items that took seconds
        # per box. A fixed width in characters keeps opening a form instant.
        self.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.setMinimumContentsLength(28)
        self.setModel(model if model is not None else item_model(items or []))
        # with the app's style sheet Qt otherwise measures all 20,000 rows of the drop-down list (1.3 s per box)
        view = self.view()
        if hasattr(view, "setUniformItemSizes"):
            view.setUniformItemSizes(True)
        c = self.completer()
        c.setFilterMode(Qt.MatchContains)
        c.setCompletionMode(QCompleter.PopupCompletion)
        c.setCaseSensitivity(Qt.CaseInsensitive)

    def value(self):
        return self.currentData(Qt.UserRole)

    def set_value(self, item_id):
        i = self.findData(item_id, Qt.UserRole)       # searched inside Qt: fast even with 20,000 items
        if i >= 0:
            self.setCurrentIndex(i)


class LinesTable(QTableWidget):
    """columns: list of (key, label, kind) where kind in item, unit, qty, money, text, bool, readonly, date.

    unit_source(item) -> list of units (core.units.Unit) for the "unit" column; on_unit(table, row, item, unit)
    is called when the person picks another unit."""
    changed = Signal()

    def __init__(self, columns: list[tuple], items: list[dict], on_item=None, unit_source=None, on_unit=None,
                 model: QStandardItemModel | None = None, by_id: dict | None = None):
        super().__init__(0, len(columns))
        self.cols = columns
        self.items = items
        # several tables on one screen can share one item list (model + by_id), built once
        self.by_id = by_id if by_id is not None else {it["id"]: it for it in items}
        self.model_ = model if model is not None else item_model(items)
        self.on_item = on_item
        self.unit_source = unit_source
        self.on_unit = on_unit
        self.setHorizontalHeaderLabels([c[1] for c in columns])
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(34)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.horizontalHeader()
        # every header fits in full (bold text + padding); the item picker takes the rest
        bold = QFont(hh.font())
        bold.setBold(True)
        fm = QFontMetrics(bold)
        for i, c in enumerate(columns):
            if c[2] == "item":
                hh.setSectionResizeMode(i, QHeaderView.Stretch)
                hh.resizeSection(i, max(220, fm.horizontalAdvance(c[1]) + 30))
            else:
                base = {"bool": 70, "readonly": 140, "unit": 130}.get(c[2], 100)
                self.setColumnWidth(i, max(base, fm.horizontalAdvance(c[1]) + 30))
            self.horizontalHeaderItem(i).setToolTip(c[1])

    def find_row(self, item_id, unit: str | None = None) -> int:
        """The row that already holds this item (and unit), or -1."""
        item_ci = next((i for i, c in enumerate(self.cols) if c[2] == "item"), None)
        if item_ci is None:
            return -1
        for r in range(self.rowCount()):
            if self.cellWidget(r, item_ci).value() == item_id:
                if unit is None or (self.unit_of(r)[0] or "").lower() == unit.lower():
                    return r
        return -1

    def add_row(self, values: dict | None = None):
        values = values or {}
        r = self.rowCount()
        self.insertRow(r)
        for ci, (key, lbl, kind) in enumerate(self.cols):
            if kind == "item":
                w = ItemCombo(model=self.model_)
                if values.get(key):
                    w.set_value(values[key])
                w.currentIndexChanged.connect(lambda _i, row_w=w: self._item_changed(row_w))
            elif kind == "unit":
                w = QComboBox()
                w.setToolTip("The unit this line is in. Add packs and cases to an item in Items > Packs & cases.")
                w.currentIndexChanged.connect(lambda _i, uw=w: self._unit_changed(uw))
            elif kind == "qty":
                w = QtyEdit(values.get(key) or 0)
                w.textChanged.connect(lambda *_: self.changed.emit())
            elif kind == "money":
                w = MoneyEdit(values.get(key) or 0)
                w.textChanged.connect(lambda *_: self.changed.emit())
            elif kind == "bool":
                w = QCheckBox()
                w.setChecked(bool(values.get(key, True)))
            elif kind == "readonly":
                it = QTableWidgetItem(str(values.get(key, "")))
                it.setFlags(Qt.ItemIsEnabled)
                self.setItem(r, ci, it)
                continue
            else:
                w = QLineEdit(str(values.get(key) or ""))
            self.setCellWidget(r, ci, w)
        item_key = next((c[0] for c in self.cols if c[2] == "item"), None)
        unit_key = next((c[0] for c in self.cols if c[2] == "unit"), None)
        if item_key and unit_key and values.get(item_key) in self.by_id:
            self._fill_units(r, self.by_id[values[item_key]], values.get(unit_key))
        return r

    # ---- units -------------------------------------------------------
    def _unit_col(self) -> int | None:
        return next((i for i, c in enumerate(self.cols) if c[2] == "unit"), None)

    def _fill_units(self, r: int, item, select: str | None = None) -> None:
        ci = self._unit_col()
        if ci is None or self.unit_source is None:
            return
        w = self.cellWidget(r, ci)
        w.blockSignals(True)
        w.clear()
        for u in self.unit_source(item):
            w.addItem(u.label(item["unit"]), (u.code, u.factor))
        idx = 0
        if select:
            for i in range(w.count()):
                if w.itemData(i)[0].lower() == str(select).lower():
                    idx = i
        w.setCurrentIndex(idx)
        w.blockSignals(False)

    def _unit_changed(self, w):
        r = self._row_of(w)
        if r < 0:
            return
        item_ci = next((i for i, c in enumerate(self.cols) if c[2] == "item"), None)
        item = self.by_id.get(self.cellWidget(r, item_ci).value()) if item_ci is not None else None
        if item is not None and self.on_unit and w.currentData():
            from ...core.units import Unit
            code, factor = w.currentData()
            self.on_unit(self, r, item, Unit(code, factor, base=factor == 1 and code == item["unit"]))
        self.changed.emit()

    def unit_of(self, r: int) -> tuple[str | None, float]:
        ci = self._unit_col()
        if ci is None:
            return None, 1.0
        data = self.cellWidget(r, ci).currentData()
        return (data[0], float(data[1])) if data else (None, 1.0)

    def _row_of(self, widget) -> int:
        for r in range(self.rowCount()):
            for c in range(self.columnCount()):
                if self.cellWidget(r, c) is widget:
                    return r
        return -1

    def _item_changed(self, w):
        r = self._row_of(w)
        item = self.by_id.get(w.value()) if r >= 0 else None
        if item:
            self._fill_units(r, item)
        if r >= 0 and self.on_item and item:
            self.on_item(self, r, item)
        self.changed.emit()

    def set_cell(self, r: int, key: str, value):
        ci = next(i for i, c in enumerate(self.cols) if c[0] == key)
        kind = self.cols[ci][2]
        w = self.cellWidget(r, ci)
        if kind == "readonly":
            # Re-inserting an item this table already owns makes Qt warn
            # ("cannot insert an item that is already owned by another
            # QTableWidget"), so only call setItem() for a brand-new item.
            it = self.item(r, ci)
            if it is None:
                it = QTableWidgetItem(str(value))
                it.setFlags(Qt.ItemIsEnabled)
                self.setItem(r, ci, it)
            else:
                it.setText(str(value))
                it.setToolTip(str(value))
        elif kind == "money":
            w.set_cents(int(value or 0))
        elif kind == "qty":
            w.setText(f"{value:g}" if value else "")
        elif kind == "bool":
            w.setChecked(bool(value))
        elif kind == "item":
            w.set_value(value)
        elif kind == "unit":
            for i in range(w.count()):
                if w.itemData(i) and w.itemData(i)[0].lower() == str(value).lower():
                    w.setCurrentIndex(i)
        else:
            w.setText(str(value or ""))

    def remove_selected(self):
        rows = sorted({i.row() for i in self.selectedIndexes()}, reverse=True)
        if not rows and self.rowCount():
            rows = [self.rowCount() - 1]
        for r in rows:
            self.removeRow(r)
        self.changed.emit()

    def values(self) -> list[dict]:
        out = []
        for r in range(self.rowCount()):
            row = {}
            for ci, (key, lbl, kind) in enumerate(self.cols):
                w = self.cellWidget(r, ci)
                if kind == "item":
                    row[key] = w.value()
                elif kind == "qty":
                    row[key] = w.value()
                elif kind == "money":
                    row[key] = w.cents()
                elif kind == "bool":
                    row[key] = w.isChecked()
                elif kind == "readonly":
                    row[key] = self.item(r, ci).text() if self.item(r, ci) else ""
                elif kind == "unit":
                    data = w.currentData()
                    row[key], row[key + "_factor"] = (data[0], float(data[1])) if data else (None, 1.0)
                else:
                    row[key] = w.text().strip()
            if any(c[2] == "item" for c in self.cols):
                key = next(c[0] for c in self.cols if c[2] == "item")
                if not row.get(key):
                    continue
            out.append(row)
        return out

    def clear_rows(self):
        self.setRowCount(0)
        self.changed.emit()
