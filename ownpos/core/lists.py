"""Editable master lists: items, customers, vendors, employees, departments, tax codes,
payment methods, accounts and the small lookup lists (units, sizes, reasons, ...).

One field spec per list drives the spreadsheet-style editor, validation, Excel import and Excel export,
so adding a column here adds it everywhere."""
from __future__ import annotations

from dataclasses import dataclass, field

from .auth import audit
from .barcode import normalize_code
from .db import Database, now_str
from .money import round_qty, to_cents


@dataclass
class Field:
    key: str
    label: str
    type: str = "text"          # text, code (UPC / barcode kept as typed), int, money, qty, pct, bool, choice, ref
    required: bool = False
    editable: bool = True
    choices: list | None = None  # for 'choice': [(value, label)]
    ref: str | None = None       # for 'ref': a key in REF_SOURCES
    width: int = 120
    hidden: bool = False          # hidden in the grid by default (still in Excel)


@dataclass
class Entity:
    key: str
    title: str
    table: str
    pk: str
    fields: list[Field]
    match_on: list[str] = field(default_factory=list)  # for Excel import updates
    order_by: str = "name"
    has_active: bool = True
    perm: str = "lists"
    where: str = ""                                  # fixed filter, e.g. list_name for lookups
    fixed: dict = field(default_factory=dict)        # fixed values on insert


KIND_CHOICES = [("inventory", "Inventory (counted)"), ("non_inventory", "Non-inventory"), ("service", "Service")]
ACCOUNT_TYPES = [("asset", "Asset"), ("liability", "Liability"), ("equity", "Equity"), ("income", "Income"),
                 ("contra_income", "Contra income"), ("cogs", "Cost of goods sold"), ("expense", "Expense")]
MONEY_KINDS = [("cash", "Cash"), ("bank", "Bank")]
PAY_TYPES = [("hourly", "Hourly"), ("salary", "Salary (per week)"), ("none", "Not paid through the store")]

REF_SOURCES = {
    "department": ("SELECT id, name FROM departments WHERE active = 1 ORDER BY sort, name", "departments", "id", "name"),
    "vendor": ("SELECT id, name FROM vendors WHERE active = 1 ORDER BY name", "vendors", "id", "name"),
    "tax_code": ("SELECT code, name FROM tax_codes WHERE active = 1 ORDER BY code", "tax_codes", "code", "name"),
    "unit": ("SELECT code, name FROM lookups WHERE list_name = 'unit' AND active = 1 ORDER BY sort", "lookups", "code", "name"),
    "size": ("SELECT code, name FROM lookups WHERE list_name = 'size' AND active = 1 ORDER BY sort", "lookups", "code", "name"),
    "account": ("SELECT code, code || ' ' || name FROM accounts WHERE active = 1 ORDER BY code", "accounts", "code", "name"),
    "income_account": ("SELECT code, code || ' ' || name FROM accounts WHERE type = 'income' AND active = 1 ORDER BY code",
                       "accounts", "code", "name"),
    "asset_account": ("SELECT code, code || ' ' || name FROM accounts WHERE type IN ('asset','liability') AND active = 1"
                      " ORDER BY code", "accounts", "code", "name"),
    "expense_account": ("SELECT code, code || ' ' || name FROM accounts WHERE type IN ('expense','cogs','equity')"
                        " AND active = 1 ORDER BY code", "accounts", "code", "name"),
    "role": ("SELECT code, name FROM roles WHERE active = 1 ORDER BY sort, name", "roles", "code", "name"),
    "price_level": ("SELECT CAST(code AS INTEGER), name FROM lookups WHERE list_name = 'price_level' AND active = 1"
                    " ORDER BY sort", "lookups", "code", "name"),
}

ENTITIES: dict[str, Entity] = {
    "items": Entity("items", "Items", "items", "id", [
        Field("item_no", "Item #", width=80),           # empty = next free number
        Field("upc", "UPC", "code", width=120),
        Field("alt_lookup", "Alternate lookup", "code", width=110, hidden=True),
        Field("name", "Item name", required=True, width=230),
        Field("size", "Size", "ref", ref="size", width=70),
        Field("attribute", "Attribute", width=90, hidden=True),
        Field("department_id", "Department", "ref", ref="department", width=130),
        Field("kind", "Item type", "choice", choices=KIND_CHOICES, width=110, hidden=True),
        Field("unit", "Unit", "ref", ref="unit", width=60),
        Field("price1", "Regular price", "money", width=85),
        Field("price2", "Price 2", "money", width=75, hidden=True),
        Field("price3", "Price 3", "money", width=75, hidden=True),
        Field("price4", "Price 4", "money", width=75, hidden=True),
        Field("price5", "Price 5", "money", width=75, hidden=True),
        Field("msrp", "MSRP", "money", width=75, hidden=True),
        Field("tax_code", "Tax", "ref", ref="tax_code", width=70),
        Field("on_hand", "On hand", "qty", editable=False, width=75),
        Field("cost_avg", "Avg cost", "money", editable=False, width=75),
        Field("last_cost", "Last cost", "money", editable=False, width=75, hidden=True),
        Field("reorder_point", "Reorder point", "qty", width=80),
        Field("reorder_qty", "Order up to", "qty", width=80, hidden=True),
        Field("vendor_id", "Vendor", "ref", ref="vendor", width=140),
        Field("vendor_item_no", "Vendor item #", width=100, hidden=True),
        Field("on_register", "Quick button", "bool", width=70, hidden=True),
        Field("tile_color", "Button color", width=80, hidden=True),
        Field("sort", "Button order", "int", width=70, hidden=True),
        Field("notes", "Notes", width=160, hidden=True),
    ], match_on=["item_no", "upc"], perm="items"),
    "customers": Entity("customers", "Customers", "customers", "id", [
        Field("code", "Customer #", width=80),
        Field("name", "Name", required=True, width=180),
        Field("company", "Company", width=150, hidden=True),
        Field("phone", "Phone", width=110),
        Field("email", "Email", width=160),
        Field("address", "Street", width=160, hidden=True),
        Field("city", "City", width=100, hidden=True),
        Field("state", "State", width=50, hidden=True),
        Field("zip", "ZIP", width=70, hidden=True),
        Field("price_level", "Price level", "ref", ref="price_level", width=100),
        Field("discount_pct", "Discount %", "pct", width=80),
        Field("credit_limit", "Charge limit", "money", width=90),
        Field("terms_days", "Terms (days)", "int", width=80),
        Field("tax_exempt", "Tax exempt", "bool", width=70),
        Field("tax_exempt_no", "Exemption #", width=100, hidden=True),
        Field("notes", "Notes", width=160, hidden=True),
    ], match_on=["code", "phone", "email"], perm="customers"),
    "vendors": Entity("vendors", "Vendors", "vendors", "id", [
        Field("code", "Vendor code", width=90),
        Field("name", "Name", required=True, width=200),
        Field("contact", "Contact", width=130),
        Field("phone", "Phone", width=110),
        Field("email", "Email", width=160),
        Field("address", "Street", width=160, hidden=True),
        Field("city", "City", width=100, hidden=True),
        Field("state", "State", width=50, hidden=True),
        Field("zip", "ZIP", width=70, hidden=True),
        Field("account_no", "Our account #", width=100),
        Field("terms_days", "Terms (days)", "int", width=80),
        Field("notes", "Notes", width=160, hidden=True),
    ], match_on=["code", "name"], perm="receive"),
    "employees": Entity("employees", "Employees", "users", "id", [
        Field("username", "Login name", required=True, width=110),
        Field("full_name", "Full name", required=True, width=170),
        Field("title", "Job title", width=130),
        Field("role", "Role", "ref", ref="role", required=True, width=130),
        Field("phone", "Phone", width=110),
        Field("email", "Email", width=150, hidden=True),
        Field("pay_type", "Pay type", "choice", choices=PAY_TYPES, width=100),
        Field("pay_rate", "Pay rate ($/hour or $/week)", "money", width=110),
        Field("overtime", "Overtime after 40 h", "bool", width=90),
        Field("notes", "Notes", width=160, hidden=True),
    ], match_on=["username"], order_by="full_name", perm="employees"),
    "departments": Entity("departments", "Departments", "departments", "id", [
        Field("name", "Department", required=True, width=170),
        Field("code", "Code", width=70),
        Field("income_account", "Sales account", "ref", ref="income_account", width=170),
        Field("default_tax_code", "Default tax", "ref", ref="tax_code", width=110),
        Field("margin_target", "Target margin %", "pct", width=90),
        Field("color", "Button color", width=90),
        Field("show_on_register", "On register", "bool", width=80),
        Field("sort", "Order", "int", width=60),
    ], match_on=["name"], order_by="sort, name"),
    "tax_codes": Entity("tax_codes", "Tax codes", "tax_codes", "code", [
        Field("code", "Code", required=True, width=90),
        Field("name", "Name", required=True, width=220),
        Field("rate", "Rate %", "pct", width=70),
    ], match_on=["code"], order_by="code", perm="settings"),
    "payment_methods": Entity("payment_methods", "Payment methods", "payment_methods", "code", [
        Field("code", "Code", required=True, width=100),
        Field("name", "Name", required=True, width=170),
        Field("account", "Ledger account", "ref", ref="asset_account", width=220),
        Field("is_cash", "Is cash", "bool", width=60),
        Field("is_card", "Is card", "bool", width=60),
        Field("requires_ref", "Needs number", "bool", width=80),
        Field("requires_customer", "Needs customer", "bool", width=90),
        Field("opens_drawer", "Opens drawer", "bool", width=80),
        Field("on_register", "On register", "bool", width=80),
        Field("sort", "Order", "int", width=60),
    ], match_on=["code"], order_by="sort", perm="settings"),
    "accounts": Entity("accounts", "Chart of accounts", "accounts", "code", [
        Field("code", "Account #", required=True, width=80),
        Field("name", "Name", required=True, width=280),
        Field("type", "Type", "choice", choices=ACCOUNT_TYPES, required=True, width=130),
        Field("subtype", "Money kind (cash / bank register)", "choice", choices=MONEY_KINDS, width=160),
    ], match_on=["code"], order_by="code", perm="accounting"),
}

LOOKUP_LISTS = {
    "unit": "Units",
    "size": "Sizes",
    "payout_reason": "Payout reasons",
    "waste_reason": "Breakage / waste reasons",
    "adjust_reason": "Adjustment reasons",
    "return_reason": "Return reasons",
    "price_level": "Price levels",
    "discount_button": "Discount buttons",
}


def lookup_entity(list_name: str) -> Entity:
    fields = [Field("code", "Code", required=True, width=90), Field("name", "Name", required=True, width=200),
              Field("sort", "Order", "int", width=60)]
    if list_name == "payout_reason":
        fields.insert(2, Field("value", "Expense account", "ref", ref="expense_account", width=200))
    if list_name == "discount_button":
        fields.insert(2, Field("value", "Discount %", width=90))
    return Entity(f"lookup:{list_name}", LOOKUP_LISTS.get(list_name, list_name), "lookups", "id", fields,
                  match_on=["code"], order_by="sort, name", where=f"list_name = '{list_name}'",
                  fixed={"list_name": list_name})


def get_entity(key: str) -> Entity:
    if key.startswith("lookup:"):
        return lookup_entity(key.split(":", 1)[1])
    return ENTITIES[key]


class ValidationError(Exception):
    pass


class ListService:
    def __init__(self, db: Database):
        self.db = db

    def choices(self, ref: str) -> list[tuple]:
        sql = REF_SOURCES[ref][0]
        return [(r[0], r[1]) for r in self.db.all(sql)]

    def rows(self, ent: Entity, include_inactive: bool = False) -> list[dict]:
        cond = []
        if ent.where:
            cond.append(ent.where)
        if ent.has_active and not include_inactive:
            cond.append("active = 1")
        sql = f"SELECT * FROM {ent.table}"
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        sql += f" ORDER BY {ent.order_by}"
        return self.db.dicts(sql)

    def coerce(self, f: Field, value):
        """Turn user/Excel input into the stored value."""
        if isinstance(value, str) and f.type in ("int", "money", "qty", "pct"):
            for junk in ("$", "USD", " "):
                value = value.replace(junk, "")
        if f.type == "code":
            value = normalize_code(value) or None
        if value is None or (isinstance(value, str) and value.strip() == ""):
            if f.required:
                raise ValidationError(f"{f.label} is required")
            if f.type in ("int", "money", "qty", "pct", "bool"):
                return 0
            return None
        if f.type == "money":
            try:
                return to_cents(value)
            except Exception:
                raise ValidationError(f"{f.label}: '{value}' is not an amount")
        if f.type == "int":
            try:
                return int(float(str(value).replace(",", "")))
            except ValueError:
                raise ValidationError(f"{f.label}: '{value}' is not a whole number")
        if f.type in ("qty", "pct"):
            try:
                return round_qty(float(str(value).replace(",", "").replace("%", "")))
            except ValueError:
                raise ValidationError(f"{f.label}: '{value}' is not a number")
        if f.type == "bool":
            if isinstance(value, str):
                return 1 if value.strip().lower() in ("1", "yes", "y", "true", "x", "t") else 0
            return 1 if value else 0
        if f.type == "choice":
            vals = {str(v): v for v, _ in f.choices}
            labels = {str(lbl).lower(): v for v, lbl in f.choices}
            s = str(value).strip()
            if s in vals:
                return vals[s]
            if s.lower() in labels:
                return labels[s.lower()]
            raise ValidationError(f"{f.label}: '{value}' is not one of {', '.join(str(v) for v, _ in f.choices)}")
        if f.type == "ref" and f.ref == "tax_code" and isinstance(value, str):
            low = value.strip().lower()
            if low in ("tax", "taxable", "t", "y", "yes", "sales tax", "in"):
                value = "IN7"
            elif low in ("non", "non-taxable", "nontaxable", "not taxable", "exempt", "n", "no", "nt", "none"):
                value = "NT"
        if f.type == "ref":
            opts = self.choices(f.ref)
            for v, lbl in opts:
                if str(v) == str(value).strip() or str(lbl).strip().lower() == str(value).strip().lower():
                    return v
            # also accept the bare name for "code name" style labels
            for v, lbl in opts:
                if str(lbl).lower().endswith(" " + str(value).strip().lower()):
                    return v
            raise ValidationError(f"{f.label}: '{value}' was not found")
        if isinstance(value, float) and value.is_integer():
            value = int(value)  # Excel stores codes like 1001 or barcodes as numbers
        return str(value).strip()

    def save(self, ent: Entity, values: dict, user_id: int, pk=None) -> object:
        """Insert (pk None) or update one row. values use stored types (cents for money)."""
        data = {}
        for f in ent.fields:
            if f.key in values and f.editable:
                data[f.key] = values[f.key]
        for f in ent.fields:
            if f.required and pk is None and not data.get(f.key) and data.get(f.key) != 0:
                raise ValidationError(f"{f.label} is required")
        with self.db.tx():
            # checked under the store-wide write lock, so two stations cannot both pass the check
            if ent.key == "employees" and "role" in data:
                from .auth import AuthService
                auth = AuthService(self.db)
                auth.check_role_change(pk, data["role"], auth.get(user_id))
            self._check_unique(ent, data, pk)
            if pk is None:
                # an empty optional field on a NEW row takes the database's default (e.g. a department's sales account)
                data = {k: v for k, v in data.items() if v is not None}
                data.update(ent.fixed)
                if ent.key == "items":
                    data = self._item_defaults(data)
                cols = ", ".join(data)
                marks = ", ".join("?" for _ in data)
                if ent.pk == "id":
                    pk = self.db.insert(f"INSERT INTO {ent.table}({cols}) VALUES({marks})", list(data.values()))
                else:
                    self.db.exec(f"INSERT INTO {ent.table}({cols}) VALUES({marks})", list(data.values()))
                    pk = data.get(ent.pk)
                audit(self.db, user_id, "create", ent.key, pk, data)
            else:
                if not data:
                    return pk
                old = self.db.one(f"SELECT * FROM {ent.table} WHERE {ent.pk} = ?", (pk,))
                if ent.key == "items":
                    if (old is not None and "kind" in data and data["kind"] != old["kind"]
                            and (abs(float(old["on_hand"])) > 1e-9 or self.db.val(
                                "SELECT 1 FROM stock_moves WHERE item_id = ? LIMIT 1", (pk,)))):
                        raise ValidationError(f"{old['name']} already has stock history, so its item type cannot "
                                              "change. Make it inactive and add a new item instead.")
                    data["updated_at"] = now_str()
                sets = ", ".join(f"{k} = ?" for k in data)
                self.db.exec(f"UPDATE {ent.table} SET {sets} WHERE {ent.pk} = ?", list(data.values()) + [pk])
                changes = {k: [old[k] if old is not None and k in old.keys() else None, v] for k, v in data.items()
                           if old is None or k not in old.keys() or old[k] != v}
                if changes:
                    audit(self.db, user_id, "update", ent.key, pk, changes)
                if ent.key == "items" and old is not None:
                    from .pricing import PRICE_FIELDS, PricingService
                    ps = PricingService(self.db)
                    for k in PRICE_FIELDS:
                        if k in changes:
                            ps.record_single(pk, k, old[k], data[k], user_id)
        return pk

    def set_active(self, ent: Entity, pk, active: bool, user_id: int) -> None:
        if not ent.has_active:
            return
        with self.db.tx():
            if ent.key == "employees" and not active:
                owners = self.db.val("SELECT COUNT(*) FROM users WHERE role = 'owner' AND active = 1 AND id <> ?",
                                     (pk,), 0)
                role = self.db.val("SELECT role FROM users WHERE id = ?", (pk,))
                if role == "owner" and owners == 0:
                    raise ValidationError("At least one active owner must remain")
            self.db.exec(f"UPDATE {ent.table} SET active = ? WHERE {ent.pk} = ?", (1 if active else 0, pk))
            audit(self.db, user_id, "activate" if active else "deactivate", ent.key, pk, {})

    def _check_unique(self, ent: Entity, data: dict, pk) -> None:
        uniques = {"items": ["item_no"], "customers": ["code"], "vendors": ["code"], "employees": ["username"],
                   "departments": ["name"], "tax_codes": ["code"], "payment_methods": ["code"], "accounts": ["code"]}
        keys = uniques.get(ent.key, ["code"] if ent.table == "lookups" else [])
        for k in keys:
            v = data.get(k)
            if v in (None, ""):
                continue
            sql = f"SELECT {ent.pk} FROM {ent.table} WHERE lower(CAST({k} AS TEXT)) = lower(?)"
            args = [v]
            if ent.where:
                sql += f" AND {ent.where}"
            row = self.db.one(sql, args)
            if row is not None and (pk is None or str(row[0]) != str(pk)):
                label = next((f.label for f in ent.fields if f.key == k), k)
                raise ValidationError(f"{label} '{v}' is already used")
        if ent.key == "items" and data.get("upc"):
            # compared without leading zeros: 012345678905 and 12345678905 are the same product code
            key = str(data["upc"]).lstrip("0") or str(data["upc"])
            row = self.db.one("SELECT id, name FROM items WHERE upc_key = ? AND active = 1", (key,))
            if row is not None and (pk is None or row["id"] != pk):
                raise ValidationError(f"UPC {data['upc']} already belongs to {row['name']}")
            row = self.db.one("SELECT i.id, i.name FROM item_units u JOIN items i ON i.id = u.item_id"
                              " WHERE u.barcode_key = ? AND i.active = 1", (key,))
            if row is not None:
                raise ValidationError(f"UPC {data['upc']} is already a pack/case barcode of {row['name']}")

    def _item_defaults(self, data: dict) -> dict:
        """New items take their tax code from their department; item # is the next free number."""
        dept = (self.db.one("SELECT * FROM departments WHERE id = ?", (data.get("department_id"),))
                if data.get("department_id") else None)
        if not data.get("tax_code"):
            data["tax_code"] = (dept["default_tax_code"] if dept is not None else None) or "IN7"
        if not data.get("kind"):
            data["kind"] = "inventory"
        if not data.get("unit"):
            data["unit"] = "ea"
        if not data.get("item_no"):
            data["item_no"] = self.next_item_no()
        return data

    def next_item_no(self) -> str:
        n = self.db.val("SELECT MAX(CAST(item_no AS BIGINT)) FROM items WHERE item_no ~ '^[0-9]{1,15}$'",
                        default=1000) or 1000
        return str(int(n) + 1)

    next_sku = next_item_no
