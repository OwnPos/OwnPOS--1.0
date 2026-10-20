"""Editable lists: items, customers, vendors, employees, departments and every lookup list.

Edit straight in the grid like a spreadsheet, add rows with a form, deactivate
rows you no longer use, and import or export any list as Excel."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget, QHeaderView, QComboBox)

from ...core.excel import commit_import, export_list, read_import, write_template
from ...core.lists import LOOKUP_LISTS, Entity, get_entity
from ...core.money import fmt_money
from ..dialogs import BaseDialog
from ..widgets.common import (MoneyEdit, QtyEdit, RefCombo, button, confirm, error, guarded, hbox, info, label,
                              page_header, vbox)
from ..widgets.grid import Col, DataGrid

TYPE_MAP = {"money": "money", "qty": "qty", "pct": "pct", "int": "int", "bool": "bool"}


class EntityForm(BaseDialog):
    def __init__(self, parent, svc, ent: Entity, values: dict | None = None, title: str = "", win=None):
        super().__init__(parent, title or f"New {ent.title.lower().rstrip('s')}", 520)
        self.svc = svc
        self.ent = ent
        self.widgets = {}
        values = values or {}
        form = QFormLayout()
        for f in ent.fields:
            if not f.editable:
                continue
            v = values.get(f.key)
            if f.type == "money":
                w = MoneyEdit(v or 0)
            elif f.type in ("qty", "pct"):
                w = QtyEdit(v or 0, 3)
            elif f.type == "int":
                w = QSpinBox()
                w.setRange(-1000000, 100000000)
                w.setValue(int(v or 0))
            elif f.type == "bool":
                w = QCheckBox()
                w.setChecked(bool(v))
            elif f.type in ("ref", "choice"):
                choices = svc.choices(f.ref) if f.type == "ref" else f.choices
                on_new = None
                if f.type == "ref" and win is not None:
                    from ..typenew import make_creator
                    on_new = make_creator(self, win, f.ref)
                w = RefCombo(choices, blank="" if not f.required else None, on_new=on_new)
                if v is not None:
                    w.set_value(v)
            else:
                w = QLineEdit("" if v is None else str(v))
            self.widgets[f.key] = (f, w)
            form.addRow(f.label + (" *" if f.required else ""), w)
        scroll = QScrollArea()
        inner = QWidget()
        inner.setLayout(form)
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(min(60 + 38 * len(self.widgets), 560))
        self.lay.addWidget(scroll)
        self.buttons("Save")

    def values(self) -> dict:
        out = {}
        for key, (f, w) in self.widgets.items():
            if f.type == "money":
                out[key] = w.cents()
            elif f.type in ("qty", "pct"):
                out[key] = w.value()
            elif f.type == "int":
                out[key] = w.value()
            elif f.type == "bool":
                out[key] = 1 if w.isChecked() else 0
            elif f.type in ("ref", "choice"):
                out[key] = w.value()
            else:
                out[key] = w.text().strip() or None
        return out

    def validate(self):
        for key, (f, w) in self.widgets.items():
            v = self.values()[key]
            if f.required and (v is None or v == ""):
                error(self, f"{f.label} is required")
                return False
        return True


PAY_FIELDS = ("pay_type", "pay_rate", "overtime")


class EntityPage(QWidget):
    allow_add = True                 # Price levels: the five levels can be renamed, not added to

    def __init__(self, win, entity_key: str, compact: bool = False, title: str | None = None):
        super().__init__()
        self.setObjectName("page")
        self.win = win
        self.s = win.s
        self.ent = get_entity(entity_key)
        self.page_title = title or self.ent.title
        self.can_edit = win.user.can(self.ent.perm)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0 if compact else 20, 0 if compact else 16, 0 if compact else 20, 0 if compact else 16)
        lay.setSpacing(10)
        self.show_inactive = QCheckBox("Show inactive")
        self.show_inactive.toggled.connect(self.reload)
        btns = []
        if self.can_edit:
            if self.allow_add:
                btns.append(button("Add", "primary", self.add))
            btns.append(button("Edit form", slot=self.edit_form))
            if self.ent.has_active and self.allow_add:
                btns.append(button("Make inactive / active", slot=self.toggle_active,
                                   tip="Inactive rows are hidden from lists and the register; their history stays"))
        btns += self.extra_buttons()
        if win.user.can("import") and self.can_edit:
            btns.append(button("Import from Excel", slot=self.import_excel))
        if win.user.can("export"):
            btns.append(button("Export to Excel", slot=self.export_excel))
        subtitle = ("Edit cells directly like a spreadsheet (double-click or start typing). "
                    "Right-click a header to choose columns.") if self.can_edit else ""
        if compact:
            lay.addLayout(hbox(label(self.page_title, "sectionTitle"), None, self.show_inactive, *btns))
            lay.addWidget(label(subtitle, "muted"))
        else:
            lay.addLayout(page_header(self.page_title, subtitle, self.show_inactive, *btns))
        self.grid = DataGrid(self.columns(), key=f"list_{entity_key}", on_edit=self.on_edit if self.can_edit else None,
                             toolbar=True)
        self.grid.activated.connect(lambda r: self.edit_form())
        lay.addWidget(self.grid, 1)
        self.reload()
        self._fresh = True              # just loaded: the first on_show need not load it all again

    def extra_buttons(self):
        if self.ent.key in ("customers", "vendors"):
            return [button("History", slot=self.party_history,
                           tip="Receipts / vouchers and the account of the selected " + self.ent.key[:-1])]
        return []

    @guarded
    def party_history(self):
        row = self.grid.current_row()
        if row is None:
            error(self, "Click a row first")
            return
        from .history_page import PartyHistoryDialog
        PartyHistoryDialog(self, self.s, self.ent.key[:-1], row["id"]).exec()

    def hidden_fields(self) -> set:
        """Fields this person may not see or change (pay is only for people with the payroll permission)."""
        if self.ent.key == "employees" and not self.win.user.can("payroll"):
            return set(PAY_FIELDS)
        return set()

    def form_entity(self) -> Entity:
        hide = self.hidden_fields()
        if not hide:
            return self.ent
        from dataclasses import replace
        return replace(self.ent, fields=[f for f in self.ent.fields if f.key not in hide])

    def columns(self):
        cols = []
        hide = self.hidden_fields()
        for f in self.ent.fields:
            if f.key in hide:
                continue
            choices = None
            if f.type == "ref":
                choices = self.s.lists.choices(f.ref)
            elif f.type == "choice":
                choices = f.choices
            on_new = None
            if f.type == "ref" and f.editable and self.can_edit:
                from ..typenew import make_creator
                on_new = make_creator(self, self.win, f.ref)
            cols.append(Col(f.key, f.label, TYPE_MAP.get(f.type, "text"), editable=f.editable and self.can_edit,
                            choices=choices, width=f.width, hidden=f.hidden, total=False, on_new=on_new))
        if self.ent.key == "customers":
            cols.append(Col("_owes", "Owes us", "money", width=100))
        if self.ent.key == "vendors":
            cols.append(Col("_owed", "We owe", "money", width=100))
        if self.ent.has_active:
            cols.append(Col("_status", "Status", "text", width=80, hidden=True))
        return cols

    def on_show(self):
        if getattr(self, "_fresh", False):
            self._fresh = False
            return
        self.grid.set_columns(self.columns())
        self.reload()

    def keep_row(self, row) -> bool:
        return True

    def reload(self):
        rows = [r for r in self.s.lists.rows(self.ent, self.show_inactive.isChecked()) if self.keep_row(r)]
        for r in rows:
            r["_status"] = "Active" if r.get("active", 1) else "Inactive"
            if not r.get("active", 1):
                r["_muted"] = True
        if self.ent.key == "customers":
            bal = {r["party_id"]: r["b"] for r in self.s.db.all(
                "SELECT party_id, SUM(debit - credit) b FROM journal_lines WHERE account = '1200' AND party_type = 'customer'"
                " GROUP BY party_id")}
            for r in rows:
                r["_owes"] = bal.get(r["id"], 0)
        if self.ent.key == "vendors":
            bal = {r["party_id"]: r["b"] for r in self.s.db.all(
                "SELECT party_id, SUM(credit - debit) b FROM journal_lines WHERE account = '2000' AND party_type = 'vendor'"
                " GROUP BY party_id")}
            for r in rows:
                r["_owed"] = bal.get(r["id"], 0)
        self.grid.set_rows(rows)

    def on_edit(self, row, col: Col, value):
        f = next(x for x in self.ent.fields if x.key == col.key)
        stored = self.s.lists.coerce(f, value)
        self.s.lists.save(self.ent, {col.key: stored}, self.win.user.id, row[self.ent.pk])
        self.after_change()
        return stored

    def after_change(self):
        if self.ent.key in ("items", "departments") and "register" in self.win.pages:
            reg = self.win.pages["register"]
            reg.refresh_tiles()
            reg.refresh_suggestions()
        if self.ent.key == "lookup:discount_button" and "register" in self.win.pages:
            self.win.pages["register"]._discount_buttons()

    @guarded
    def add(self):
        d = EntityForm(self, self.s.lists, self.form_entity(), win=self.win)
        if d.exec() == QDialog.Accepted:
            self.s.lists.save(self.ent, d.values(), self.win.user.id)
            self.reload()
            self.after_change()

    @guarded
    def edit_form(self):
        row = self.grid.current_row()
        if row is None or not self.can_edit:
            return
        title = row.get("name") or row.get("full_name") or row.get(self.ent.pk)
        d = EntityForm(self, self.s.lists, self.form_entity(), row, f"Edit {title}", win=self.win)
        if d.exec() == QDialog.Accepted:
            self.s.lists.save(self.ent, d.values(), self.win.user.id, row[self.ent.pk])
            self.reload()
            self.after_change()

    @guarded
    def toggle_active(self):
        rows = self.grid.selected_rows()
        if not rows:
            error(self, "Click the row first (Ctrl+click or Shift+click for several)")
            return
        for r in rows:
            self.s.lists.set_active(self.ent, r[self.ent.pk], not r.get("active", 1), self.win.user.id)
        self.reload()
        self.after_change()

    @guarded
    def export_excel(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export to Excel", f"{self.ent.title}.xlsx", "Excel workbook (*.xlsx)")
        if path:
            self.grid.export_to(path, self.ent.title, self.s.db.get_setting("company_name", ""))
            self.win.toast(f"Saved {path}")

    @guarded
    def import_excel(self):
        box = QMessageBox(self)
        box.setWindowTitle(f"Import {self.ent.title}")
        box.setText("Choose an Excel file to import, or first download a template (with your current rows to edit).")
        imp = box.addButton("Choose file…", QMessageBox.AcceptRole)
        tmpl = box.addButton("Download template", QMessageBox.ActionRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() == tmpl:
            path, _ = QFileDialog.getSaveFileName(self, "Save template", f"{self.ent.title} template.xlsx",
                                                  "Excel workbook (*.xlsx)")
            if path:
                write_template(path, self.s.lists, self.ent, with_rows=True)
                info(self, f"Template saved to {path}. Fill or edit it in Excel, then import it.")
            return
        if box.clickedButton() != imp:
            return
        path, _ = QFileDialog.getOpenFileName(self, "Import from Excel or CSV", "",
                                              "Excel or CSV (*.xlsx *.xls *.csv);;All files (*)")
        if not path:
            return
        from ..import_dialog import ImportMapDialog
        m = ImportMapDialog(self, self.s, self.ent, path)
        if m.exec() != QDialog.Accepted:
            return
        rows = read_import(path, self.s.lists, self.ent, m.mapping(), m.header_row)
        d = ImportPreview(self, self.ent, rows)
        if d.exec() == QDialog.Accepted:
            res = commit_import(self.s.lists, self.ent, rows, self.win.user.id, services=self.s,
                                opening_date=m.opening_iso())
            msg = f"Added {res['added']}, updated {res['updated']}."
            if res.get("openings"):
                msg += f" Opening amounts posted for {res['openings']} new row(s)."
            if res["failed"]:
                msg += f" {res['failed']} row(s) failed:\n" + "\n".join(res["errors"][:10])
            info(self, msg)
            self.reload()
            self.after_change()


class ImportPreview(BaseDialog):
    def __init__(self, parent, ent, rows):
        super().__init__(parent, f"Check the import: {ent.title}", 820)
        adds = sum(r.action == "add" for r in rows)
        upd = sum(r.action == "update" for r in rows)
        skip = sum(r.action == "skip" for r in rows)
        self.lay.addWidget(label(f"{adds} new · {upd} updates · {skip} with errors (will be skipped)", "sectionTitle"))
        self.lay.addWidget(label("Nothing is saved until you press Import. Departments, vendors and sizes named in the "
                                 "file that do not exist yet are created. Rows marked Skip are not imported: fix them "
                                 "in the file and import it again (rows already imported are updated, not doubled).",
                                 "muted", wrap=True))
        t = QTableWidget(len(rows), 3)
        t.setHorizontalHeaderLabels(["Excel row", "Action", "Details"])
        t.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        t.verticalHeader().setVisible(False)
        for i, r in enumerate(rows):
            t.setItem(i, 0, QTableWidgetItem(str(r.row_no)))
            t.setItem(i, 1, QTableWidgetItem({"add": "Add", "update": "Update", "skip": "Skip"}[r.action]))
            detail = "; ".join(r.errors) if r.errors else ", ".join(f"{k}={getattr(v, 'name', v)}" for k, v in list(r.values.items())[:6])
            if r.extra:
                detail += "   opening: " + ", ".join(f"{k.strip('_')}={v}" for k, v in r.extra.items())
            if r.notes:
                detail += "   (" + "; ".join(r.notes) + ")"
            t.setItem(i, 2, QTableWidgetItem(detail))
        self.lay.addWidget(t, 1)
        self.buttons(f"Import {adds + upd} row(s)")
        self.resize(860, 520)


class EmployeesPage(EntityPage):
    """Employees: login, role, job title and pay. Pay is visible only with the payroll permission."""
    def __init__(self, win):
        super().__init__(win, "employees")

    def extra_buttons(self):
        out = [button("Set password", slot=lambda: self.set_secret("password")),
               button("Set PIN", slot=lambda: self.set_secret("pin"))]
        if self.win.user.role == "owner":            # only the owner gives or takes away permissions
            out.append(button("Roles and Permissions", slot=self.edit_roles,
                              tip="Choose what each role may do; give or take away a permission in one click"))
        out.append(button("Time clock", slot=lambda: self.win.go("timeclock")))
        return out

    @guarded
    def edit_roles(self):
        from .roles_dialog import RolesDialog
        RolesDialog(self, self.s, self.win.user).exec()
        self.on_show()               # the Role column's drop-down picks up new or renamed roles

    @guarded
    def add(self):
        d = NewEmployee(self, self.s.auth.role_choices(), self.win.user.can("payroll"))
        if d.exec() == QDialog.Accepted:
            self.s.auth.create_user(d.user.text().strip(), d.full.text().strip(), d.role.value(), d.pw.text(),
                                    d.pin.text(), d.phone.text().strip(), self.win.user, title=d.title.text().strip(),
                                    pay_type=d.pay_type.value() or "hourly", pay_rate=d.pay_rate.cents())
            self.reload()

    @guarded
    def set_secret(self, kind):
        row = self.grid.current_row()
        if row is None:
            error(self, "Click the employee first")
            return
        from ..widgets.common import SecretDialog
        d = SecretDialog(self, f"New {kind}", f"New {kind} for {row['full_name']}:")
        if d.exec() != QDialog.Accepted or not d.text():
            return
        if kind == "pin" and (not d.text().isdigit() or not 4 <= len(d.text()) <= 8):
            error(self, "A PIN is 4 to 8 digits")
            return
        if kind == "password" and len(d.text()) < 8:
            error(self, "Use at least 8 characters")
            return
        self.s.auth.set_secret(row["id"], password=d.text() if kind == "password" else None,
                               pin=d.text() if kind == "pin" else None, actor=self.win.user)
        info(self, f"{kind.capitalize()} changed.")


class NewEmployee(BaseDialog):
    def __init__(self, parent, roles, show_pay: bool):
        super().__init__(parent, "New employee", 440)
        from ...core.lists import PAY_TYPES
        self.user = QLineEdit()
        self.full = QLineEdit()
        self.title = QLineEdit()
        self.title.setPlaceholderText("e.g. Cashier, Stock clerk, Store manager")
        self.phone = QLineEdit()
        self.role = RefCombo(roles)
        self.role.set_value("cashier")
        self.pay_type = RefCombo(PAY_TYPES)
        self.pay_rate = MoneyEdit(0)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setPlaceholderText("At least 8 characters (back office)")
        self.pin = QLineEdit()
        self.pin.setEchoMode(QLineEdit.Password)
        self.pin.setPlaceholderText("4 to 8 digits (register and time clock)")
        form = QFormLayout()
        rows = [("Login name *", self.user), ("Full name *", self.full), ("Job title", self.title),
                ("Phone", self.phone), ("Role *", self.role)]
        if show_pay:
            rows += [("Pay type", self.pay_type), ("Pay rate ($/hour or $/week)", self.pay_rate)]
        rows += [("Password", self.pw), ("PIN", self.pin)]
        for lbl, w in rows:
            form.addRow(lbl, w)
        self.lay.addLayout(form)
        self.lay.addWidget(label("Give a password (back office) and/or a PIN (quick sign-in at the register and the "
                                 "time clock). What the role may do is set in Roles and Permissions.", "muted", wrap=True))
        self.buttons("Create")

    def validate(self):
        if not self.user.text().strip() or not self.full.text().strip():
            error(self, "Login name and full name are required")
            return False
        if not self.pw.text() and not self.pin.text():
            error(self, "Give a password or a PIN")
            return False
        if self.pw.text() and len(self.pw.text()) < 8:
            error(self, "The password needs at least 8 characters")
            return False
        if self.pin.text() and (not self.pin.text().isdigit() or not 4 <= len(self.pin.text()) <= 8):
            error(self, "A PIN is 4 to 8 digits")
            return False
        return True


class PriceLevelsPage(EntityPage):
    """The five price levels: rename them (e.g. "Employee", "Bar account"); there are always exactly five."""
    allow_add = False

    def columns(self):
        cols = super().columns()
        for c in cols:
            if c.key == "code":
                c.editable = False
        return cols


class ListsHub(QWidget):
    """All the small lists in one place."""
    def __init__(self, win):
        super().__init__()
        self.setObjectName("page")
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.addLayout(page_header("Lists", "Departments, sizes, units, discount buttons, reasons, price levels, tax "
                                  "codes, payment methods and the chart of accounts. Changes apply on every station."))
        body = QHBoxLayout()
        self.menu = QListWidget()
        self.menu.setFixedWidth(220)
        self.keys = [("departments", "Departments")]
        self.keys += [(f"lookup:{k}", v) for k, v in LOOKUP_LISTS.items()]
        self.keys += [("tax_codes", "Tax codes"), ("payment_methods", "Payment methods"), ("accounts", "Chart of accounts")]
        for key, title in self.keys:
            self.menu.addItem(title)
        body.addWidget(self.menu)
        self.holder = QVBoxLayout()
        body.addLayout(self.holder, 1)
        lay.addLayout(body, 1)
        self.current = None
        self.menu.currentRowChanged.connect(self.open)
        self.menu.setCurrentRow(0)

    def open(self, i):
        if self.current is not None:
            self.current.setParent(None)
            self.current.deleteLater()
        key = self.keys[i][0]
        cls = PriceLevelsPage if key == "lookup:price_level" else EntityPage
        self.current = cls(self.win, key, compact=True)
        self.holder.addWidget(self.current)
