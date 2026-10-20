"""Employees > Roles and Permissions: create roles and tick what each one may do.

Only the owner can change roles. Everyone else who opens this screen can look but not change.
The owner role always has every permission, so its boxes are ticked and locked."""
from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QScrollArea, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt

from ...core.auth import PERMISSION_GROUPS, PERMISSIONS
from ..dialogs import BaseDialog
from ..widgets.common import button, confirm, error, guarded, hbox, info, label


class RolesDialog(BaseDialog):
    def __init__(self, parent, services, user):
        super().__init__(parent, "Roles and Permissions", 900)
        self.s = services
        self.user = user
        self.can_edit = user.role == "owner"
        self.code = None          # the role being edited; None = a new role not saved yet

        # left: the list of roles, with New / Remove under it
        self.list = QListWidget()
        self.list.setFixedWidth(220)
        self.list.currentItemChanged.connect(self.show_role)
        left = QVBoxLayout()
        left.addWidget(self.list, 1)
        self.new_btn = button("New role", slot=self.new_role)
        self.del_btn = button("Remove role", slot=self.remove_role)
        left.addLayout(hbox(self.new_btn, self.del_btn))

        # right: the role's name and one tick box per permission, in groups
        self.name = QLineEdit()
        form = QFormLayout()
        form.addRow("Role name *", self.name)
        self.note = label("", "muted", wrap=True)
        self.boxes: dict[str, QCheckBox] = {}
        groups = QWidget()
        glay = QVBoxLayout(groups)
        glay.setContentsMargins(0, 0, 0, 0)
        for title, codes in PERMISSION_GROUPS:
            box = QGroupBox(title)
            grid = QGridLayout(box)
            for i, code in enumerate(codes):
                cb = QCheckBox(PERMISSIONS[code])
                self.boxes[code] = cb
                grid.addWidget(cb, i // 2, i % 2)      # two columns
            glay.addWidget(box)
        glay.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidget(groups)
        scroll.setWidgetResizable(True)
        self.save_btn = button("Save role", "primary", self.save_role)
        right = QVBoxLayout()
        right.addLayout(form)
        right.addWidget(self.note)
        right.addWidget(scroll, 1)
        right.addLayout(hbox(button("Tick all", slot=lambda: self.tick(True)),
                             button("Clear all", slot=lambda: self.tick(False)), None, self.save_btn))

        body = QHBoxLayout()
        body.addLayout(left)
        body.addLayout(right, 1)
        self.lay.addLayout(body, 1)
        if not self.can_edit:
            self.lay.addWidget(label("Only the owner can change roles. You can look at them here.", "muted"))
        self.buttons("Close", cancel=False)
        self.resize(940, 680)
        self.reload()

    # ---- showing ----------------------------------------------------------
    def reload(self, select: str | None = None):
        self.list.blockSignals(True)
        self.list.clear()
        for r in self.s.auth.roles():
            users = self.s.db.val("SELECT COUNT(*) FROM users WHERE role = ? AND active = 1", (r["code"],), default=0)
            it = QListWidgetItem(f"{r['name']}   ({users})")
            it.setData(Qt.UserRole, r["code"])
            self.list.addItem(it)
        self.list.blockSignals(False)
        row = 0
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole) == select:
                row = i
        self.list.setCurrentRow(row)
        self.show_role()

    def show_role(self, *_):
        it = self.list.currentItem()
        if it is None:
            return
        self.code = it.data(Qt.UserRole)
        r = self.s.db.one("SELECT * FROM roles WHERE code = ?", (self.code,))
        self.name.setText(r["name"])
        perms = self.s.auth.role_permissions(self.code)
        is_owner = self.code == "owner"
        for code, cb in self.boxes.items():
            cb.setChecked(code in perms)
            cb.setEnabled(self.can_edit and not is_owner)
        self.name.setEnabled(self.can_edit)
        self.save_btn.setEnabled(self.can_edit)
        self.new_btn.setEnabled(self.can_edit)
        self.del_btn.setEnabled(self.can_edit and not is_owner)
        self.note.setText("The owner can always do everything." if is_owner else
                          f"{len(perms)} of {len(PERMISSIONS)} permissions. "
                          "Changes apply the next time each employee logs in.")

    def tick(self, on: bool):
        if self.can_edit and self.code != "owner":
            for cb in self.boxes.values():
                cb.setChecked(on)

    # ---- changing -----------------------------------------------------------
    def new_role(self):
        self.list.clearSelection()
        self.code = None
        self.name.setText("")
        for cb in self.boxes.values():
            cb.setChecked(False)
            cb.setEnabled(True)
        self.del_btn.setEnabled(False)
        self.note.setText("New role: give it a name, tick what it may do, then Save role.")
        self.name.setFocus()

    @guarded
    def save_role(self):
        perms = {code for code, cb in self.boxes.items() if cb.isChecked()}
        if not perms and not confirm(self, "This role has no permissions: its employees could log in but do "
                                           "nothing. Save anyway?"):
            return
        code = self.s.auth.save_role(self.code, self.name.text(), perms, self.user)
        self.reload(select=code)
        info(self, "Role saved.")

    @guarded
    def remove_role(self):
        if self.code is None or not confirm(self, f"Remove the role '{self.name.text()}'?"):
            return
        self.s.auth.delete_role(self.code, self.user)
        self.reload()
