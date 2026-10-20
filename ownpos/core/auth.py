"""Employees, roles, permissions and the hash-chained audit log."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass, field

from .db import Database, now_str

# The starting roles of a new store. After that, roles live in the database (table 'roles')
# and the owner edits them in Employees > Roles & permissions.
ROLES = {
    "owner": "Owner",
    "manager": "Manager",
    "cashier": "Cashier",
    "stock": "Stock clerk",
}

# Every permission the program checks. The owner always has all of them.
PERMISSIONS = {
    "sell": "Ring up sales",
    "discount": "Give discounts up to the cashier limit (employee 10/15/20% buttons)",
    "discount_any": "Give any discount",
    "price_override": "Change a price at the register",
    "return": "Return items from a receipt",
    "void_receipt": "Void a whole receipt",
    "no_sale": "Open the drawer without a sale",
    "payout": "Pay out cash from the drawer",
    "shift": "Open and close a drawer (Z-Out)",
    "x_report": "Run an X-Out report",
    "house_charge": "Put sales on a house charge account",
    "customers": "Add and edit customers",
    "items": "Add and edit items and prices",
    "costs": "See item costs and margins",
    "lists": "Edit departments and other lists",
    "receive": "Receive inventory",
    "purchase_orders": "Create purchase orders",
    "vendor_payments": "Pay vendor bills",
    "waste": "Record breakage and waste",
    "adjust": "Adjust stock and physical counts",
    "reports": "View sales and inventory reports",
    "financial_reports": "View financial reports (profit and loss, balance sheet)",
    "accounting": "Post accounting entries (expenses, transfers, journals)",
    "employees": "Manage employees and passwords",
    "timeclock_edit": "Fix time clock punches",
    "payroll": "See pay rates and the payroll report",
    "settings": "Change settings",
    "agents": "Manage agents",
    "assistant": "Use the AI assistant",
    "export": "Export to Excel / PDF",
    "import": "Import from Excel",
    "backup": "Back up and restore",
}

PERMISSION_GROUPS = [
    ("Selling", ["sell", "discount", "discount_any", "price_override", "return", "void_receipt", "no_sale",
                 "payout", "shift", "x_report", "house_charge"]),
    ("Customers and items", ["customers", "items", "costs", "lists"]),
    ("Inventory", ["receive", "purchase_orders", "vendor_payments", "waste", "adjust"]),
    ("Money and reports", ["reports", "financial_reports", "accounting", "export", "import", "backup"]),
    ("Employees", ["employees", "timeclock_edit", "payroll"]),
    ("Administration", ["settings", "agents", "assistant"]),
]

ROLE_PERMISSIONS = {
    "owner": set(PERMISSIONS),
    "manager": set(PERMISSIONS) - {"employees", "settings", "payroll", "backup"},
    "cashier": {"sell", "discount", "return", "shift", "x_report", "customers", "waste"},
    "stock": {"receive", "purchase_orders", "waste", "adjust", "items", "reports"},
}


def ensure_roles(db: Database) -> None:
    """Add the starting roles to a database that has none of them yet. Existing roles are never changed."""
    for i, (code, name) in enumerate(ROLES.items()):
        perms = ",".join(sorted(ROLE_PERMISSIONS[code]))
        db.exec("INSERT INTO roles(code, name, permissions, sort) VALUES(?,?,?,?) ON CONFLICT(code) DO NOTHING",
                (code, name, perms, i + 1))


def hash_secret(secret: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, 120_000)
    return f"pbkdf2${salt.hex()}${dk.hex()}"


def verify_secret(secret: str, stored: str | None) -> bool:
    if not stored or not secret:
        return False
    try:
        _, salt_hex, dk_hex = stored.split("$")
    except ValueError:
        return False
    test = hash_secret(secret, bytes.fromhex(salt_hex)).split("$")[2]
    return hmac.compare_digest(test, dk_hex)


@dataclass
class User:
    id: int
    username: str
    full_name: str
    role: str
    perms: set = field(default_factory=set)
    title: str = ""

    def can(self, perm: str) -> bool:
        return perm in self.perms


class AuthService:
    def __init__(self, db: Database):
        self.db = db

    # ---- PIN index ----------------------------------------------------------------------------
    def _pin_key(self) -> bytes:
        """A random secret per store, so the PIN index below cannot be compared between stores."""
        key = self.db.val("SELECT value FROM meta WHERE key = 'pin_key'")
        if not key:
            key = os.urandom(16).hex()
            self.db.exec("INSERT INTO meta(key, value) VALUES('pin_key', ?) ON CONFLICT(key) DO NOTHING", (key,))
            key = self.db.val("SELECT value FROM meta WHERE key = 'pin_key'")
        return bytes.fromhex(key)

    def _pin_index(self, pin: str) -> str:
        """Fast lookup key for a PIN (one HMAC), so logging in by PIN does not try every employee's hash."""
        return hmac.new(self._pin_key(), pin.encode("utf-8"), hashlib.sha256).hexdigest()

    # ---- roles --------------------------------------------------------------------------------
    def roles(self, include_inactive: bool = False) -> list[dict]:
        sql = "SELECT * FROM roles" + ("" if include_inactive else " WHERE active = 1") + " ORDER BY sort, name"
        return self.db.dicts(sql)

    def role_choices(self) -> list[tuple]:
        return [(r["code"], r["name"]) for r in self.roles()]

    def role_name(self, code: str) -> str:
        return self.db.val("SELECT name FROM roles WHERE code = ?", (code,), default=code)

    def role_permissions(self, code: str) -> set:
        if code == "owner":
            return set(PERMISSIONS)
        text = self.db.val("SELECT permissions FROM roles WHERE code = ? AND active = 1", (code,), default="")
        return {p.strip() for p in text.split(",") if p.strip() in PERMISSIONS}

    def save_role(self, code: str | None, name: str, permissions: set, actor: User) -> str:
        """Create (code None) or update a role. Only the owner may do this. Returns the role's code."""
        if actor.role != "owner":
            raise ValueError("Only the owner can change roles")
        name = name.strip()
        if not name:
            raise ValueError("Give the role a name")
        clash = self.db.val("SELECT code FROM roles WHERE lower(name) = lower(?)", (name,))
        if clash and clash != code:
            raise ValueError(f"There is already a role called '{name}'")
        perms = ",".join(sorted(p for p in permissions if p in PERMISSIONS))
        with self.db.tx():
            if code is None:
                code = self._new_role_code(name)
                sort = self.db.val("SELECT COALESCE(MAX(sort), 0) + 1 FROM roles", default=1)
                self.db.exec("INSERT INTO roles(code, name, permissions, sort) VALUES(?,?,?,?)",
                             (code, name, perms, sort))
                audit(self.db, actor.id, "role_create", "role", code, {"name": name, "permissions": perms})
            else:
                if code == "owner":
                    perms = ",".join(sorted(PERMISSIONS))
                self.db.exec("UPDATE roles SET name = ?, permissions = ? WHERE code = ?", (name, perms, code))
                audit(self.db, actor.id, "role_update", "role", code, {"name": name, "permissions": perms})
        return code

    def delete_role(self, code: str, actor: User) -> None:
        if actor.role != "owner":
            raise ValueError("Only the owner can change roles")
        if code == "owner":
            raise ValueError("The owner role cannot be removed")
        users = [r["full_name"] for r in self.db.all(
            "SELECT full_name FROM users WHERE role = ? AND active = 1 ORDER BY full_name", (code,))]
        if users:
            raise ValueError("Give these employees another role first: " + ", ".join(users))
        with self.db.tx():
            self.db.exec("UPDATE roles SET active = 0 WHERE code = ?", (code,))
            audit(self.db, actor.id, "role_delete", "role", code, {})

    def _new_role_code(self, name: str) -> str:
        base = "".join(ch if ch.isalnum() else "_" for ch in name.lower()).strip("_")[:20] or "role"
        code, n = base, 2
        while self.db.val("SELECT 1 FROM roles WHERE code = ?", (code,)):
            code, n = f"{base}_{n}", n + 1
        return code

    def check_role_change(self, user_id: int | None, new_role: str, actor: User | None) -> None:
        if not self.db.val("SELECT 1 FROM roles WHERE code = ? AND active = 1", (new_role,)):
            raise ValueError(f"Unknown role {new_role}")
        if new_role == "owner" and actor is not None and actor.role != "owner":
            raise ValueError("Only the owner can make someone an owner")
        if user_id is not None and new_role != "owner":
            old = self.db.val("SELECT role FROM users WHERE id = ?", (user_id,))
            others = self.db.val("SELECT COUNT(*) FROM users WHERE role = 'owner' AND active = 1 AND id <> ?",
                                 (user_id,), default=0)
            if old == "owner" and others == 0:
                raise ValueError("At least one active owner must remain")

    # ---- users --------------------------------------------------------------------------------
    def has_users(self) -> bool:
        return bool(self.db.val("SELECT COUNT(*) FROM users WHERE active = 1", default=0))

    def _user(self, row) -> User:
        return User(row["id"], row["username"], row["full_name"], row["role"], self.role_permissions(row["role"]),
                    row["title"] or "")

    def get(self, user_id: int) -> User | None:
        row = self.db.one("SELECT * FROM users WHERE id = ?", (user_id,))
        return self._user(row) if row else None

    def login(self, username: str, secret: str) -> User | None:
        row = self.db.one("SELECT * FROM users WHERE lower(username) = lower(?) AND active = 1", (username.strip(),))
        ok = row is not None and (verify_secret(secret, row["password_hash"]) or verify_secret(secret, row["pin_hash"]))
        audit(self.db, None if row is None else row["id"], "login" if ok else "login_failed", "user", username, {})
        return self._user(row) if ok else None

    def login_pin(self, pin: str) -> User | None:
        """Quick register login by PIN alone (PINs are unique per active employee)."""
        row = self.db.one("SELECT * FROM users WHERE active = 1 AND pin_index = ?", (self._pin_index(pin),))
        if row is not None and verify_secret(pin, row["pin_hash"]):
            audit(self.db, row["id"], "login_pin", "user", row["username"], {})
            return self._user(row)
        audit(self.db, None, "login_failed", "user", "pin", {})
        return None

    def authorize(self, pin_or_password: str, perm: str) -> User | None:
        """Manager approval at the register: the approving employee if the PIN/password matches and they hold perm."""
        row = self.db.one("SELECT * FROM users WHERE active = 1 AND pin_index = ?", (self._pin_index(pin_or_password),))
        candidates = [row] if row is not None else self.db.all(
            "SELECT * FROM users WHERE active = 1 AND password_hash IS NOT NULL AND role IN"
            " (SELECT code FROM roles WHERE code = 'owner' OR permissions LIKE ?)", (f"%{perm}%",))
        for r in candidates:
            if verify_secret(pin_or_password, r["pin_hash"]) or verify_secret(pin_or_password, r["password_hash"]):
                u = self._user(r)
                if u.can(perm):
                    return u
        return None

    def _check_pin(self, pin: str) -> None:
        if not pin.isdigit() or not 4 <= len(pin) <= 8:
            raise ValueError("A PIN is 4 to 8 digits")

    def create_user(self, username: str, full_name: str, role: str, password: str = "", pin: str = "",
                    phone: str = "", actor: User | None = None, title: str = "", pay_type: str = "hourly",
                    pay_rate: int = 0) -> int:
        self.check_role_change(None, role, actor)
        username = username.strip()
        if not username or not full_name.strip():
            raise ValueError("Give the employee a login name and a full name")
        if not password and not pin:
            raise ValueError("Give the employee a password or a PIN")
        if pin:
            self._check_pin(pin)
        with self.db.tx():
            if self.db.val("SELECT 1 FROM users WHERE lower(username) = lower(?)", (username,)):
                raise ValueError(f"The login name '{username}' is already used")
            if pin and self.db.val("SELECT 1 FROM users WHERE active = 1 AND pin_index = ?", (self._pin_index(pin),)):
                raise ValueError("That PIN is already used by another employee")
            uid = self.db.insert(
                "INSERT INTO users(username, full_name, title, role, phone, password_hash, pin_hash, pin_index,"
                " pay_type, pay_rate) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (username, full_name.strip(), title or None, role, phone or None,
                 hash_secret(password) if password else None, hash_secret(pin) if pin else None,
                 self._pin_index(pin) if pin else None, pay_type, int(pay_rate or 0)))
            audit(self.db, actor.id if actor else uid, "user_create", "user", uid, {"username": username, "role": role})
        return uid

    def set_secret(self, user_id: int, password: str | None = None, pin: str | None = None,
                   actor: User | None = None) -> None:
        row = self.db.one("SELECT username, role FROM users WHERE id = ?", (user_id,))
        if row is None:
            raise ValueError("Employee not found")
        if row["role"] == "owner" and actor is not None and actor.id != user_id and actor.role != "owner":
            raise ValueError("Only an owner can change an owner's password or PIN")
        if pin:
            self._check_pin(pin)
        with self.db.tx():
            if password:
                self.db.exec("UPDATE users SET password_hash = ? WHERE id = ?", (hash_secret(password), user_id))
            if pin:
                idx = self._pin_index(pin)
                if self.db.val("SELECT 1 FROM users WHERE active = 1 AND pin_index = ? AND id <> ?", (idx, user_id)):
                    raise ValueError("That PIN is already used by another employee")
                self.db.exec("UPDATE users SET pin_hash = ?, pin_index = ? WHERE id = ?", (hash_secret(pin), idx, user_id))
            audit(self.db, actor.id if actor else user_id, "user_secret_change", "user", user_id,
                  {"password": bool(password), "pin": bool(pin)})


# ---- audit log -----------------------------------------------------------------------------------------

def audit(db: Database, user_id, action: str, entity: str | None = None, entity_id=None,
          details: dict | None = None) -> None:
    """Append a tamper-evident entry: each hash covers the previous entry's hash.
    Runs under the store-wide write lock, so entries from different stations chain in order."""
    with db.tx():
        prev = db.val("SELECT hash FROM audit_log ORDER BY id DESC LIMIT 1", default="GENESIS")
        username = db.val("SELECT username FROM users WHERE id = ?", (user_id,)) if user_id else None
        ts = now_str()
        payload = json.dumps(details or {}, ensure_ascii=False, sort_keys=True, default=str)
        raw = f"{prev}|{ts}|{user_id}|{action}|{entity}|{entity_id}|{payload}"
        h = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        db.exec("INSERT INTO audit_log(ts, user_id, username, station_id, action, entity, entity_id, details,"
                " prev_hash, hash) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (ts, user_id, username, db.station_id, action, entity,
                 None if entity_id is None else str(entity_id), payload, prev, h))


def verify_audit_chain(db: Database) -> tuple[bool, int | None]:
    """Recompute every hash. Returns (ok, first_bad_id)."""
    prev = "GENESIS"
    last_id = 0
    while True:
        rows = db.all("SELECT * FROM audit_log WHERE id > ? ORDER BY id LIMIT 5000", (last_id,))
        if not rows:
            return True, None
        for r in rows:
            raw = f"{prev}|{r['ts']}|{r['user_id']}|{r['action']}|{r['entity']}|{r['entity_id']}|{r['details']}"
            if r["prev_hash"] != prev or hashlib.sha256(raw.encode("utf-8")).hexdigest() != r["hash"]:
                return False, r["id"]
            prev = r["hash"]
            last_id = r["id"]
