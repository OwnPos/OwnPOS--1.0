"""Pricing management (QuickBooks POS Pro "Pricing Management"): change many prices at
once by percent, by a fixed amount, to a set price, or to a target margin over cost,
with rounding. Every change is kept in price_changes and the audit log."""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .auth import audit
from .db import Database, now_str

PRICE_FIELDS = {"price1": "Regular price", "price2": "Price level 2", "price3": "Price level 3",
                "price4": "Price level 4", "price5": "Price level 5"}
MODES = {
    "pct": "Raise or lower by %",
    "amount": "Raise or lower by $",
    "set": "Set to $",
    "margin": "Set margin % of price over average cost",
}
ROUNDING = {0: "No rounding", 1: "Nearest cent", 99: "End in .99", 49: "End in .49 / .99", 100: "Nearest dollar"}


class PricingError(Exception):
    pass


def round_to(cents: float, step: int) -> int:
    """Round a price in cents. step 0/1: to the cent; 100: to the dollar (half-up); 99: up to the next .99;
    49: up to the next .49 or .99 (the usual retail endings)."""
    c = int(Decimal(str(cents)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if step in (0, 1):
        return c
    if step == 99:
        return (c // 100) * 100 + 99 if c % 100 <= 99 else c
    if step == 49:
        base = (c // 100) * 100
        return base + 49 if c % 100 <= 49 else base + 99
    return int((Decimal(str(cents)) / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP)) * step


class PricingService:
    def __init__(self, db: Database):
        self.db = db

    def preview(self, item_ids: list[int], field: str, mode: str, value: float, rounding: int = 100) -> list[dict]:
        """Return the proposed changes without saving: item, old price, new price."""
        if field not in PRICE_FIELDS:
            raise PricingError("Unknown price field")
        if mode not in MODES:
            raise PricingError("Unknown way of changing prices")
        if not item_ids:
            raise PricingError("Choose at least one item")
        rows = self.db.dicts(f"SELECT i.id, i.item_no, i.name, i.size, i.unit, i.cost_avg, i.last_cost, i.price1,"
                             f" i.{field} AS old FROM items i WHERE i.id = ANY(?) ORDER BY i.name", (list(item_ids),))
        out = []
        for r in rows:
            old = int(r["old"] or 0)
            cost = float(r["cost_avg"] or r["last_cost"] or 0)
            if old == 0 and field != "price1":
                old_eff = int(r["price1"] or 0)      # an empty level sells at the Regular price
            else:
                old_eff = old
            if mode == "pct":
                new = old_eff * (1 + value / 100.0)
            elif mode == "amount":
                new = old_eff + value * 100
            elif mode == "set":
                new = value * 100
            else:  # margin % of the price, over average cost
                if value >= 100:
                    raise PricingError("Margin must be under 100%")
                new = old_eff if cost <= 0 else cost / (1 - value / 100.0)
            new = max(0, round_to(new, rounding))
            out.append({"item_id": r["id"], "item_no": r["item_no"], "name": r["name"], "size": r["size"],
                        "unit": r["unit"], "cost": round(cost), "old": old, "new": new,
                        "change": new - old, "below_cost": cost > 0 and 0 < new < cost})
        return out

    def apply(self, changes: list[dict], field: str, user_id: int, reason: str = "") -> int:
        """Save the previewed changes. Returns how many prices changed."""
        if field not in PRICE_FIELDS:
            raise PricingError("Unknown price field")
        real = [c for c in changes if int(c["new"]) != int(c["old"])]
        if not real:
            return 0
        with self.db.tx():
            for c in real:  # refuse a stale preview (other field chosen, or the price changed since)
                now = self.db.val(f"SELECT {field} FROM items WHERE id = ?", (c["item_id"],))
                if now is None or int(now) != int(c["old"]):
                    raise PricingError("Prices changed since the preview. Press Preview again, then Apply.")
            batch_no = f"PRC-{self.db.next_seq('PRC'):05d}"
            ts = now_str()
            for c in real:
                self.db.exec(f"UPDATE items SET {field} = ?, updated_at = ? WHERE id = ?", (int(c["new"]), ts, c["item_id"]))
                self.db.exec("INSERT INTO price_changes(item_id, field, old_price, new_price, reason, batch_no, user_id,"
                             " changed_at) VALUES(?,?,?,?,?,?,?,?)",
                             (c["item_id"], field, int(c["old"]), int(c["new"]), reason or None, batch_no, user_id, ts))
            audit(self.db, user_id, "price_change", "items", batch_no,
                  {"field": field, "items": len(real), "reason": reason})
        return len(real)

    def record_single(self, item_id: int, field: str, old: int, new: int, user_id: int, reason: str = "Edited in list"):
        """Called when one price is typed into the items grid or the item form."""
        if field in PRICE_FIELDS and int(old or 0) != int(new or 0):
            self.db.exec("INSERT INTO price_changes(item_id, field, old_price, new_price, reason, user_id, changed_at)"
                         " VALUES(?,?,?,?,?,?,?)", (item_id, field, int(old or 0), int(new or 0), reason, user_id, now_str()))

    def history(self, item_id: int | None = None, limit: int = 500) -> list[dict]:
        sql = ("SELECT p.changed_at, i.item_no, i.name, p.field, p.old_price, p.new_price, p.reason, p.batch_no, u.username"
               " FROM price_changes p JOIN items i ON i.id = p.item_id LEFT JOIN users u ON u.id = p.user_id")
        params: list = []
        if item_id:
            sql += " WHERE p.item_id = ?"
            params.append(item_id)
        sql += " ORDER BY p.id DESC LIMIT ?"
        params.append(limit)
        return self.db.dicts(sql, params)
