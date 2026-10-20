"""Make a sale > More… > Arrange quick buttons: choose which items are touch buttons, their order and color."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QColorDialog, QHBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from .dialogs import BaseDialog
from .widgets.common import button, label


class QuickLayoutDialog(BaseDialog):
    def __init__(self, parent, services, user_id: int):
        super().__init__(parent, "Arrange quick buttons", 820)
        self.s = services
        self.user_id = user_id
        items = services.db.dicts(
            "SELECT i.id, i.item_no, i.name, i.size, i.on_register, i.sort, i.tile_color, d.color AS cat_color"
            " FROM items i LEFT JOIN departments d ON d.id = i.department_id"
            " WHERE i.active = 1 ORDER BY i.sort, i.name")
        self.find = QLineEdit()
        self.find.setPlaceholderText("Search items…")
        self.find.textChanged.connect(self._filter)
        self.all = QListWidget()
        self.all.setSelectionMode(QListWidget.ExtendedSelection)
        self.quick = QListWidget()
        self.quick.setDragDropMode(QListWidget.InternalMove)      # drag to change the order
        self.quick.setSelectionMode(QListWidget.ExtendedSelection)
        for it in items:
            target = self.quick if it["on_register"] else self.all
            target.addItem(self._entry(it))
        left = QVBoxLayout()
        left.addWidget(label("All items", "sectionTitle"))
        left.addWidget(self.find)
        left.addWidget(self.all, 1)
        middle = QVBoxLayout()
        middle.addStretch(1)
        middle.addWidget(button("Add →", slot=self.add))
        middle.addWidget(button("← Remove", slot=self.remove))
        middle.addStretch(1)
        right = QVBoxLayout()
        right.addWidget(label("Quick buttons, in order (drag to reorder)", "sectionTitle"))
        right.addWidget(self.quick, 1)
        right.addLayout(self._row(button("Move up", slot=lambda: self.move(-1)),
                                  button("Move down", slot=lambda: self.move(1)),
                                  button("Colour…", slot=self.color), button("Category color", slot=self.no_color)))
        body = QHBoxLayout()
        body.addLayout(left, 1)
        body.addLayout(middle)
        body.addLayout(right, 1)
        self.lay.addLayout(body, 1)
        self.lay.addWidget(label("The sale screen shows the quick buttons in this order under '★ All quick items'. "
                                 "A color here overrides the department color for that one button.", "muted", wrap=True))
        self.buttons("Save layout")
        self.resize(860, 600)

    @staticmethod
    def _row(*widgets):
        h = QHBoxLayout()
        for w in widgets:
            h.addWidget(w)
        h.addStretch(1)
        return h

    def _entry(self, it) -> QListWidgetItem:
        e = QListWidgetItem(f"{it['name']}" + (f" {it['size']}" if it["size"] else "") + f"   #{it['item_no']}")
        e.setData(Qt.UserRole, it["id"])
        e.setData(Qt.UserRole + 1, it["tile_color"] or "")
        e.setData(Qt.UserRole + 2, it["cat_color"] or "")
        self._paint(e)
        return e

    @staticmethod
    def _paint(e: QListWidgetItem):
        color = e.data(Qt.UserRole + 1) or e.data(Qt.UserRole + 2)
        e.setForeground(QBrush(QColor(color))) if color else None

    def _filter(self, text):
        t = text.strip().lower()
        for i in range(self.all.count()):
            self.all.item(i).setHidden(bool(t) and t not in self.all.item(i).text().lower())

    @staticmethod
    def _transfer(src: QListWidget, row: int, dst: QListWidget, at: int | None = None) -> None:
        """Move one entry. A copy goes into the other list and the original is dropped, so each list
        always owns its own entries (moving the same object between lists can crash Qt on exit)."""
        copy = src.item(row).clone()
        src.takeItem(row)
        if at is None:
            dst.addItem(copy)
        else:
            dst.insertItem(at, copy)

    def add(self):
        for row in sorted((self.all.row(e) for e in self.all.selectedItems()), reverse=True):
            self._transfer(self.all, row, self.quick)

    def remove(self):
        for row in sorted((self.quick.row(e) for e in self.quick.selectedItems()), reverse=True):
            self._transfer(self.quick, row, self.all)
        self.all.sortItems()

    def move(self, step: int):
        row = self.quick.currentRow()
        new = row + step
        if row < 0 or not 0 <= new < self.quick.count():
            return
        self._transfer(self.quick, row, self.quick, new)
        self.quick.setCurrentRow(new)

    def color(self):
        e = self.quick.currentItem()
        if e is None:
            return
        c = QColorDialog.getColor(QColor(e.data(Qt.UserRole + 1) or e.data(Qt.UserRole + 2) or "#888888"), self,
                                  "Button color")
        if c.isValid():
            e.setData(Qt.UserRole + 1, c.name())
            self._paint(e)

    def no_color(self):
        e = self.quick.currentItem()
        if e is not None:
            e.setData(Qt.UserRole + 1, "")
            self._paint(e)

    def validate(self) -> bool:
        from ..core.auth import audit
        with self.s.db.tx():
            for i in range(self.quick.count()):
                e = self.quick.item(i)
                self.s.db.exec("UPDATE items SET on_register = 1, sort = ?, tile_color = ? WHERE id = ?",
                               ((i + 1) * 10, e.data(Qt.UserRole + 1) or None, e.data(Qt.UserRole)))
            for i in range(self.all.count()):
                self.s.db.exec("UPDATE items SET on_register = 0 WHERE id = ?", (self.all.item(i).data(Qt.UserRole),))
            audit(self.s.db, self.user_id, "quick_layout", "items", None, {"buttons": self.quick.count()})
        return True
