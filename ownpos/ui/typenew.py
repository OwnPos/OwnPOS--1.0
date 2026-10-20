"""Type for New: when a name typed in a drop-down is not in the list, a small form opens to add it.

make_creator(...) returns the function a RefCombo (or a grid column) calls with the typed text. It opens the
normal entry form of that list (department, size, unit, vendor, customer, account, tax code…),
already filled with what was typed. Saving adds the entry and selects it; Cancel adds nothing.
The person needs the list's permission (or a manager's approval, as everywhere else)."""
from __future__ import annotations

from PySide6.QtWidgets import QDialog

from ..core.lists import get_entity

# drop-down source (REF_SOURCES key in core/lists.py) -> (entity key, extra values for a new entry)
REF_ENTITY = {
    "department": ("departments", {}),
    "size": ("lookup:size", {}),
    "vendor": ("vendors", {}),
    "customer": ("customers", {}),
    "tax_code": ("tax_codes", {}),
    "unit": ("lookup:unit", {}),
    "account": ("accounts", {}),
    "income_account": ("accounts", {"type": "income"}),
    "asset_account": ("accounts", {"type": "asset"}),
    "expense_account": ("accounts", {"type": "expense"}),
}
ACCOUNT_RANGES = {"asset": 1000, "liability": 2000, "equity": 3000, "income": 4000, "contra_income": 4900,
                  "cogs": 5000, "expense": 6000}


def _next_account_code(db, typ: str) -> str:
    base = ACCOUNT_RANGES.get(typ, 6000)
    top = db.val("SELECT MAX(n) FROM (SELECT CASE WHEN code ~ '^[0-9]{1,9}$' THEN CAST(code AS INTEGER) END AS n"
                 " FROM accounts) t WHERE n BETWEEN ? AND ?", (base, base + 999), base) or base
    code = int(top) + 10
    while db.val("SELECT 1 FROM accounts WHERE code = ?", (str(code),)):
        code += 1
    return str(code)


def _prefill(db, ent_key: str, extra: dict, text: str) -> dict:
    text = text.strip()
    values = dict(extra)
    if ent_key == "accounts":
        values.setdefault("type", "expense")
        values["code"] = _next_account_code(db, values["type"])
        values["name"] = text
    elif ent_key == "tax_codes":
        values.update({"code": "".join(ch for ch in text.upper() if ch.isalnum())[:12], "name": text})
    elif ent_key == "lookup:unit":
        values.update({"code": "".join(ch for ch in text.lower() if ch.isalnum())[:10], "name": text})
    elif ent_key == "lookup:size":
        values.update({"code": "".join(ch for ch in text.upper() if ch.isalnum() or ch == ".")[:12], "name": text})
    elif ent_key == "departments":
        values.update({"name": text, "income_account": "4000", "default_tax_code": "IN7"})
    else:
        values["name"] = text
    return values


def make_creator(parent, win, ref: str):
    """The on_new function for a drop-down fed by REF_SOURCES[ref], or None when that list cannot be added to here."""
    if ref not in REF_ENTITY or win is None:
        return None
    ent_key, extra = REF_ENTITY[ref]

    def create(text: str):
        from .pages.lists_page import EntityForm
        from .widgets.common import error, require
        ent = get_entity(ent_key)
        if require(win, ent.perm, f"Adding to {ent.title}") is None:
            return None
        svc = win.s.lists
        d = EntityForm(parent, svc, ent, _prefill(win.s.db, ent_key, extra, text), f"New: {ent.title}", win=win)
        if d.exec() != QDialog.Accepted:
            return None
        try:
            values = d.values()
            pk = svc.save(ent, values, win.user.id)
        except Exception as exc:  # noqa: BLE001 - e.g. a name already used
            error(parent, str(exc))
            return None
        value = values.get("code") if ent_key.startswith("lookup:") else pk
        label = next((lbl for v, lbl in svc.choices(ref) if str(v) == str(value)), str(value)) \
            if ref in _ref_keys() else values.get("name") or str(value)
        if hasattr(win, "pages") and "register" in win.pages and ent_key == "departments":
            win.pages["register"].refresh_tiles()
        return value, label

    return create


def _ref_keys():
    from ..core.lists import REF_SOURCES
    return REF_SOURCES
