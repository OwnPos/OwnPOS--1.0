"""Excel import, step 1: match the file's columns to the list's fields.

Each field of the list (Item name, UPC, Regular price…) gets a drop-down of the file's columns, already guessed from
the headings. Check them, change any that are wrong, and the layout can be remembered for the next file
with the same headings. Step 2 (ImportPreview) then shows every row before anything is saved."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QFormLayout, QScrollArea, QWidget

from ..core.excel import EXTRA_FIELDS, import_fields, read_header
from .dialogs import BaseDialog
from .widgets.common import DateEdit, RefCombo, error, label


def _column_name(i: int) -> str:
    name = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        name = chr(65 + r) + name
    return name


class ImportMapDialog(BaseDialog):
    def __init__(self, parent, services, ent, path):
        super().__init__(parent, f"Import {ent.title}: match the columns", 680)
        self.s = services
        self.ent = ent
        self.path = path
        layouts = services.db.get_setting("import_layouts", {}) or {}
        self.header_row, self.headings, samples, guess = read_header(path, ent, layouts.get(ent.key))
        sample = samples[0] if samples else []
        cols = []
        for i, h in enumerate(self.headings):
            ex = sample[i] if i < len(sample) and sample[i] not in (None, "") else ""
            text = f"{_column_name(i)}: {h or '(no heading)'}" + (f"   e.g. {str(ex)[:30]}" if ex != "" else "")
            cols.append((i, text))
        self.lay.addWidget(label(f"Headings found on row {self.header_row + 1} of the file. For each field, choose the "
                                 "column that holds it (or leave it empty). Fields with * are needed for new rows.",
                                 "muted", wrap=True))
        form = QFormLayout()
        self.combos = {}
        for f in import_fields(ent):
            combo = RefCombo(cols, blank="— not in this file —")
            if f.key in guess:
                combo.set_value(guess[f.key])
            else:
                combo.setCurrentIndex(0)
            self.combos[f.key] = (f, combo)
            if ent.key == "items" and f.key == "item_no":
                form.addRow("Item # (next free number if empty)", combo)
            else:
                form.addRow(f.label + (" *" if f.required else ""), combo)
        self.opening_date = None
        if ent.key in EXTRA_FIELDS and ent.key != "items":
            self.opening_date = DateEdit()
            form.addRow("Date of the opening balances", self.opening_date)
        inner = QWidget()
        inner.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidget(inner)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(min(80 + 40 * len(self.combos), 380))
        self.lay.addWidget(scroll)
        if ent.key in EXTRA_FIELDS:
            when = ("Opening stock is recorded with today's date, so import it on the day you start using OwnPOS "
                    "(after the last sale in QuickBooks POS). " if ent.key == "items" else "")
            self.lay.addWidget(label(when + "Opening amounts (stock quantity and cost, or what a customer owes or what you owe a vendor) "
                                     "are added ONLY for rows that are new to the list, so importing the same file "
                                     "twice never doubles them.", "muted", wrap=True))
        self.remember = QCheckBox("Remember this layout for files with the same headings")
        self.remember.setChecked(True)
        self.lay.addWidget(self.remember)
        self.buttons("Next: check the rows")
        self.resize(700, 760)

    def validate(self) -> bool:
        chosen = [c.value() for _f, c in self.combos.values() if c.value() is not None]
        if len(chosen) != len(set(chosen)):
            error(self, "The same column is chosen for two fields. Each column can fill one field.")
            return False
        if not chosen:
            error(self, "Choose at least one column")
            return False
        if self.remember.isChecked():
            layouts = self.s.db.get_setting("import_layouts", {}) or {}
            layouts[self.ent.key] = {k: self.headings[c.value()] for k, (_f, c) in self.combos.items()
                                     if c.value() is not None}
            self.s.db.set_setting("import_layouts", layouts)
        return True

    def mapping(self) -> dict:
        return {k: c.value() for k, (_f, c) in self.combos.items() if c.value() is not None}

    def opening_iso(self) -> str | None:
        return self.opening_date.iso() if self.opening_date is not None else None
