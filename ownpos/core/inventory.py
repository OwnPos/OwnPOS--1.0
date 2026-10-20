"""Stock on hand and moving-average cost. One stock count per item.

Every change to stock is a row in stock_moves, written in the same transaction as the change to
items.on_hand, so the Items list always shows the real on-hand the moment a document is saved.
Packs and cases are units of the item (see units.py): they move qty x factor of the item's own unit."""
from __future__ import annotations

from .db import Database, now_str, today_str
from .money import round_cents, round_qty

INVENTORY_ACCOUNT = "1300"
STOCKED_KINDS = {"inventory"}


def inventory_account(kind: str | None = None) -> str | None:
    return INVENTORY_ACCOUNT if kind in (None, "inventory") else None


class Inventory:
    def __init__(self, db: Database, ledger=None):
        self.db = db
        self.ledger = ledger

    def item(self, item_id: int):
        return self.db.one("SELECT * FROM items WHERE id = ?", (item_id,))

    def is_stocked(self, item) -> bool:
        return item is not None and item["kind"] in STOCKED_KINDS

    def _revalue(self, item, old_qty: float, old_avg: float, unit_cost: float, user_id) -> None:
        """Receiving onto negative stock: the units sold before stock arrived were costed at the old average.
        Post the difference so the inventory account keeps matching the stock list."""
        if old_qty >= 0 or self.ledger is None:
            return
        diff = round_cents(old_qty * (unit_cost - old_avg))   # old_qty is negative
        if not diff:
            return
        from .ledger import Line
        lines = ([Line(INVENTORY_ACCOUNT, debit=diff), Line("5100", credit=diff)] if diff > 0
                 else [Line("5100", debit=-diff), Line(INVENTORY_ACCOUNT, credit=-diff)])
        self.ledger.post(today_str(), "REVAL", item["id"], None,
                         f"Cost correction after selling below zero: {item['name']}", lines, user_id)

    # ---- inbound ------------------------------------------------------------------------------
    def receive(self, item_id: int, qty: float, unit_cost: float, kind: str, doc_type: str, doc_id,
                user_id=None, note: str | None = None) -> int:
        """Add stock at unit_cost (cents per item unit). Returns the value added in cents."""
        qty = round_qty(qty)
        value = round_cents(qty * unit_cost)
        with self.db.tx():
            item = self.item(item_id)                 # read under the write lock: always the latest
            if not self.is_stocked(item):
                return 0
            old_qty, old_avg = float(item["on_hand"]), float(item["cost_avg"])
            new_qty = round_qty(old_qty + qty)
            if old_qty <= 0 or new_qty <= 0:
                new_avg = unit_cost
            else:
                new_avg = (old_qty * old_avg + qty * unit_cost) / new_qty
            # updated_at is NOT touched here: it means "the item record was edited" (the register reloads its
            # search list when it changes); stock movements are in stock_moves
            self.db.exec("UPDATE items SET on_hand = ?, cost_avg = ?, last_cost = ? WHERE id = ?",
                         (new_qty, new_avg, unit_cost if kind in ("receive", "opening") else item["last_cost"],
                          item_id))
            self._revalue(item, old_qty, old_avg, unit_cost, user_id)
            self.db.exec("INSERT INTO stock_moves(ts, item_id, qty, unit_cost, value, kind, doc_type, doc_id,"
                         " user_id, note) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (now_str(), item_id, qty, unit_cost, value, kind, doc_type, doc_id, user_id, note))
        return value

    # ---- outbound -----------------------------------------------------------------------------
    def issue(self, item_id: int, qty: float, kind: str, doc_type: str, doc_id, user_id=None,
              note: str | None = None) -> tuple[float, int]:
        """Remove stock at moving-average cost. Returns (unit_cost, value) with value positive."""
        qty = round_qty(qty)
        with self.db.tx():
            item = self.item(item_id)
            if not self.is_stocked(item):
                return 0.0, 0
            unit_cost = float(item["cost_avg"])
            value = round_cents(qty * unit_cost)
            new_qty = round_qty(float(item["on_hand"]) - qty)
            self.db.exec("UPDATE items SET on_hand = ? WHERE id = ?", (new_qty, item_id))
            self.db.exec("INSERT INTO stock_moves(ts, item_id, qty, unit_cost, value, kind, doc_type, doc_id,"
                         " user_id, note) VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (now_str(), item_id, -qty, unit_cost, -value, kind, doc_type, doc_id, user_id, note))
        return unit_cost, value

    def return_to_stock(self, item_id: int, qty: float, unit_cost: float, doc_type: str, doc_id,
                        user_id=None, value: int | None = None) -> int:
        """Customer return put back on the shelf at its original cost.

        value: the exact cents to put back (the return line's cost, so the stock movement equals the ledger
        posting); defaults to round(qty x unit_cost). Zero-cost items are restocked too (value 0)."""
        qty = round_qty(qty)
        with self.db.tx():
            item = self.item(item_id)
            if not self.is_stocked(item):
                return 0
            if value is None:
                value = round_cents(qty * unit_cost)
            old_qty, old_avg = float(item["on_hand"]), float(item["cost_avg"])
            new_qty = round_qty(old_qty + qty)
            if old_qty <= 0 or new_qty <= 0:
                new_avg = unit_cost
            else:
                new_avg = (old_qty * old_avg + value) / new_qty
            self.db.exec("UPDATE items SET on_hand = ?, cost_avg = ? WHERE id = ?",
                         (new_qty, new_avg, item["id"]))
            self._revalue(item, old_qty, old_avg, unit_cost, user_id)
            self.db.exec("INSERT INTO stock_moves(ts, item_id, qty, unit_cost, value, kind, doc_type, doc_id,"
                         " user_id) VALUES(?,?,?,?,?,?,?,?,?)",
                         (now_str(), item["id"], qty, unit_cost, value, "return", doc_type, doc_id, user_id))
        return value

    def set_count(self, item_id: int, counted: float, doc_type: str, doc_id, user_id=None,
                  note: str | None = None) -> int:
        """Physical count: move on-hand to the counted quantity. Returns the signed value change."""
        with self.db.tx():
            item = self.item(item_id)
            if not self.is_stocked(item):
                return 0
            diff = round_qty(counted - float(item["on_hand"]))
            if diff > 0:
                cost = float(item["cost_avg"]) or float(item["last_cost"])
                return self.receive(item_id, diff, cost, "count", doc_type, doc_id, user_id, note=note)
            if diff < 0:
                _, value = self.issue(item_id, -diff, "count", doc_type, doc_id, user_id, note=note)
                return -value
            return 0

    def valuation(self) -> list[dict]:
        rows = self.db.dicts(
            "SELECT i.id, i.item_no, i.upc, i.name, i.size, i.unit, i.on_hand, i.cost_avg, d.name AS department"
            " FROM items i LEFT JOIN departments d ON d.id = i.department_id"
            " WHERE i.kind = 'inventory' AND i.active = 1 ORDER BY d.sort, d.name, i.name")
        for r in rows:
            r["value"] = round_cents(r["on_hand"] * r["cost_avg"])
        return rows
