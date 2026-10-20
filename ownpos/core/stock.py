"""Breakage/waste, stock adjustments, physical counts, opening stock and owner cost corrections."""
from __future__ import annotations

from .auth import audit
from .db import Database, now_str, today_str
from .inventory import Inventory, inventory_account
from .ledger import Ledger, Line
from .money import round_cents as round_paisa, round_qty


class StockError(Exception):
    pass


class StockService:
    def __init__(self, db: Database, ledger: Ledger, inventory: Inventory):
        self.db = db
        self.ledger = ledger
        self.inv = inventory

    def _doc(self, kind: str, user_id: int, reason: str, note: str, prefix: str | None = None) -> tuple[int, str]:
        prefix = prefix or {"waste": "WS", "adjust": "AD", "count": "PI", "opening": "OS"}[kind]
        if not self.db.in_tx:
            raise StockError("internal: document numbers are only given inside a transaction")
        doc_no = f"{prefix}-{self.db.next_seq(prefix):05d}"
        did = self.db.insert("INSERT INTO adjustments(doc_no, kind, created_at, user_id, reason, note) VALUES(?,?,?,?,?,?)",
                             (doc_no, kind, now_str(), user_id, reason or None, note or None))
        return did, doc_no

    def waste(self, lines: list[tuple[int, float]], reason: str, user_id: int, note: str = "") -> int:
        lines = [(i, round_qty(q)) for i, q in lines if q and q > 0]
        if not lines:
            raise StockError("Enter at least one item and quantity")
        with self.db.tx():
            did, doc_no = self._doc("waste", user_id, reason, note)
            jl, total = [], 0
            for item_id, qty in lines:
                item = self.inv.item(item_id)
                if not self.inv.is_stocked(item):
                    raise StockError("Only inventory items can be written off")
                _, value = self.inv.issue(item_id, qty, "waste", "WS", did, user_id, note=reason)
                if value:
                    jl += [Line("5200", debit=value), Line(inventory_account(item["kind"]), credit=value)]
                total += value
            self.db.exec("UPDATE adjustments SET value = ? WHERE id = ?", (total, did))
            self.ledger.post(today_str(), "WS", did, doc_no, f"Waste: {reason}", jl, user_id)
            audit(self.db, user_id, "waste", "adjustment", did, {"doc_no": doc_no, "value": total, "reason": reason})
        return did

    def adjust(self, item_id: int, qty_delta: float, reason: str, user_id: int, note: str = "") -> int:
        if not qty_delta:
            raise StockError("Change must not be zero")
        with self.db.tx():
            item = self.inv.item(item_id)
            if not self.inv.is_stocked(item):
                raise StockError("Only inventory items have stock")
            did, doc_no = self._doc("adjust", user_id, reason, note)
            if qty_delta > 0:
                cost = float(item["cost_avg"]) or float(item["last_cost"])
                value = self.inv.receive(item_id, qty_delta, cost, "adjust", "AD", did, user_id, note=reason)
            else:
                _, v = self.inv.issue(item_id, -qty_delta, "adjust", "AD", did, user_id, note=reason)
                value = -v
            self._post_value(did, doc_no, item, value, f"Adjustment: {reason}", user_id)
            audit(self.db, user_id, "adjust", "item", item_id, {"doc_no": doc_no, "qty": qty_delta, "reason": reason})
        return did

    def count(self, counts: dict[int, float], user_id: int, note: str = "") -> int:
        """Physical inventory: set each counted item to its counted quantity."""
        if not counts:
            raise StockError("No counts entered")
        with self.db.tx():
            did, doc_no = self._doc("count", user_id, "Physical count", note)
            jl, total = [], 0
            for item_id, counted in counts.items():
                item = self.inv.item(item_id)
                value = self.inv.set_count(item_id, round_qty(counted), "PI", did, user_id)
                if value > 0:
                    jl += [Line(inventory_account(item["kind"]), debit=value), Line("5200", credit=value)]
                elif value < 0:
                    jl += [Line("5200", debit=-value), Line(inventory_account(item["kind"]), credit=-value)]
                total += value
            self.db.exec("UPDATE adjustments SET value = ? WHERE id = ?", (total, did))
            self.ledger.post(today_str(), "PI", did, doc_no, "Physical inventory count", jl, user_id)
            audit(self.db, user_id, "physical_count", "adjustment", did, {"doc_no": doc_no, "items": len(counts), "value": total})
        return did

    def opening(self, lines: list[tuple[int, float, float]], user_id: int) -> int:
        """Opening stock on cutover day: (item_id, qty, unit_cost_cents). Credits opening balance equity."""
        with self.db.tx():
            did, doc_no = self._doc("opening", user_id, "Opening stock", "")
            jl, total = [], 0
            for item_id, qty, unit_cost in lines:
                if not qty:
                    continue
                item = self.inv.item(item_id)
                value = self.inv.receive(item_id, qty, unit_cost, "opening", "OS", did, user_id)
                jl += [Line(inventory_account(item["kind"]), debit=value), Line("3100", credit=value)]
                total += value
            self.db.exec("UPDATE adjustments SET value = ? WHERE id = ?", (total, did))
            self.ledger.post(today_str(), "OS", did, doc_no, "Opening stock", jl, user_id)
            audit(self.db, user_id, "opening_stock", "adjustment", did, {"doc_no": doc_no, "value": total})
        return did

    def correct_cost(self, item_id: int, new_cost: float, reason: str, actor) -> int | None:
        """Owner only: set an item's average cost (cents per its own unit) to the right figure.

        The stock on hand is revalued: the difference (on hand x (new - old cost)) is posted between the item's
        inventory account and 5100 Inventory cost corrections, as a cost correction document (CC-00001) with a
        zero-quantity stock movement, so the stock list, the stock reports and the accounts keep agreeing.
        Returns the document id, or None when nothing changed."""
        if actor is None or getattr(actor, "role", None) != "owner":
            raise StockError("Only the owner can correct a stock cost")
        if new_cost is None or new_cost < 0:
            raise StockError("The cost cannot be negative")
        if not (reason or "").strip():
            raise StockError("Give a reason for the cost correction")
        with self.db.tx():
            item = self.inv.item(item_id)
            if item is None or not self.inv.is_stocked(item):
                raise StockError("Only inventory items have a stock cost")
            old = float(item["cost_avg"])
            new_cost = float(new_cost)
            if abs(new_cost - old) < 1e-9:
                return None
            on_hand = float(item["on_hand"])
            value = round_paisa(on_hand * new_cost) - round_paisa(on_hand * old)
            did, doc_no = self._doc("adjust", actor.id, f"Cost correction: {reason.strip()}", "", prefix="CC")
            self.db.exec("UPDATE items SET cost_avg = ?, last_cost = CASE WHEN last_cost = 0 THEN ? ELSE last_cost END,"
                         " updated_at = ? WHERE id = ?", (new_cost, new_cost, now_str(), item_id))
            self.db.exec("INSERT INTO stock_moves(ts, item_id, qty, unit_cost, value, kind, doc_type, doc_id, user_id,"
                         " note) VALUES(?,?,0,?,?,'revalue','CC',?,?,?)",
                         (now_str(), item_id, new_cost, value, did, actor.id, reason.strip()))
            self._post_value(did, doc_no, item, value, f"Cost correction {item['name']}: {reason.strip()}", actor.id,
                             other="5100")
            audit(self.db, actor.id, "cost_correction", "item", item_id,
                  {"doc_no": doc_no, "old_cost": old, "new_cost": new_cost, "on_hand": on_hand, "value": value,
                   "reason": reason.strip()})
        return did

    def _post_value(self, did, doc_no, item, value, memo, user_id, other: str = "5200"):
        acct = inventory_account(item["kind"])
        if not acct or not value:
            return
        lines = ([Line(acct, debit=value), Line(other, credit=value)] if value > 0
                 else [Line(other, debit=-value), Line(acct, credit=-value)])
        self.db.exec("UPDATE adjustments SET value = ? WHERE id = ?", (value, did))
        self.ledger.post(today_str(), "AD", did, doc_no, memo, lines, user_id)
