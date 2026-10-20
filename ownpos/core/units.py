"""Several units for one item, all sharing ONE stock count: a 6-pack, a 12-pack and a case of 24 cans
of the same beer.

Stock is always kept in the item's own unit (items.unit, e.g. "can"). Every other unit says how many of
those it holds (its factor): 6pk = 6, case = 24. A document line (sale, receiving, purchase order, count,
waste) keeps the unit it was entered in and its factor; stock moves by qty x factor.

Units come from:
  1. the item's own unit (factor 1);
  2. the units added to the item (table item_units), each with an optional own price and barcode
     (a 6-pack usually has its own UPC and its own price, not 6 x the single can).
"""
from __future__ import annotations

from dataclasses import dataclass

from .barcode import normalize_code
from .db import Database, now_str
from .money import round_cents as round_paisa

PRICE_LEVELS = (1, 2, 3, 4, 5)


class UnitError(Exception):
    pass


@dataclass
class Unit:
    code: str
    factor: float              # base units in one of this unit
    price: int = 0             # own Regular price (cents); 0 = factor x the item's price
    barcode: str = ""
    base: bool = False
    metric: bool = False

    def label(self, base_code: str) -> str:
        if self.base:
            return self.code
        return f"{self.code} (= {self.factor:g} {base_code})"


def units_for(db: Database, item, selling: bool = False) -> list[Unit]:
    """Every unit the item can be entered in (selling is accepted for compatibility; all units can be sold)."""
    base = item["unit"]
    out = [Unit(base, 1.0, base=True)]
    seen = {base.lower()}
    for r in db.all("SELECT unit, factor, price, barcode FROM item_units WHERE item_id = ? ORDER BY sort, id",
                    (item["id"],)):
        if r["unit"].lower() in seen:
            continue
        seen.add(r["unit"].lower())
        out.append(Unit(r["unit"], float(r["factor"]), int(r["price"] or 0), r["barcode"] or ""))
    return out


def get_unit(db: Database, item, code: str | None) -> Unit:
    """The unit called code for this item (the base unit when code is empty). Raises UnitError if unknown."""
    if not code or code.lower() == item["unit"].lower():
        return Unit(item["unit"], 1.0, base=True)
    for u in units_for(db, item):
        if u.code.lower() == code.lower():
            return u
    raise UnitError(f"{item['name']} has no unit '{code}'. Add it in Items > Packs & cases.")


def level_price(item, level: int = 1) -> int:
    """The item's price at a price level (1 = Regular). A level left at 0 uses the Regular price."""
    level = level if level in PRICE_LEVELS else 1
    p = item[f"price{level}"]
    return int(p or item["price1"] or 0)


def unit_price(item, unit: Unit, level: int = 1) -> int:
    """Selling price for one of this unit. A unit's own price applies to the Regular price level;
    other levels use the level price x the factor, unless that would cost more than the unit's own price."""
    if unit.base:
        return level_price(item, level)
    calc = round_paisa(float(level_price(item, level)) * unit.factor)
    if unit.price:
        return int(unit.price) if level == 1 else min(int(unit.price), calc)
    return calc


def find_by_unit_barcode(db: Database, code: str, keys: list[str] | None = None):
    """(item row, Unit) for a barcode printed on a pack, case or other unit, else None.
    keys: normalised forms of the code to try as well (see barcode.lookup_keys)."""
    code = code.strip()
    r = db.one("SELECT u.unit, u.item_id FROM item_units u JOIN items i ON i.id = u.item_id"
               " WHERE i.active = 1 AND u.barcode = ? LIMIT 1", (code,))
    if not r and keys:
        r = db.one("SELECT u.unit, u.item_id FROM item_units u JOIN items i ON i.id = u.item_id"
                   " WHERE i.active = 1 AND u.barcode_key = ANY(?) ORDER BY u.id LIMIT 1", (list(keys),))
    if not r:
        return None
    item = db.one("SELECT * FROM items WHERE id = ?", (r["item_id"],))
    return item, get_unit(db, item, r["unit"])


def save_units(db: Database, item_id: int, rows: list[dict], buy_unit: str | None = None) -> None:
    """Replace an item's extra units. rows: [{'unit', 'factor', 'price' (cents), 'barcode'}].
    buy_unit: the unit Receiving starts with (the base unit when empty)."""
    item = db.one("SELECT * FROM items WHERE id = ?", (item_id,))
    if item is None:
        raise UnitError("Item not found")
    seen, clean, seen_codes = {item["unit"].lower()}, [], set()
    for r in rows:
        code = (r.get("unit") or "").strip()
        if not code:
            continue
        try:
            factor = float(r.get("factor") or 0)
        except (TypeError, ValueError):
            factor = 0
        if factor <= 0:
            raise UnitError(f"Say how many {item['unit']} are in one {code}")
        if code.lower() in seen:
            raise UnitError(f"The unit {code} is listed twice (or is the item's own unit)")
        seen.add(code.lower())
        barcode = normalize_code(r.get("barcode"))
        if barcode:
            key = barcode.lstrip("0") or barcode          # same comparison as the register's lookup
            if key == (item["upc"] or "").lstrip("0"):
                raise UnitError(f"Barcode {barcode} is the item's own UPC (that sells one {item['unit']})")
            other = db.one("SELECT name FROM items WHERE upc_key = ? AND id <> ? AND active = 1", (key, item_id))
            other = other or db.one("SELECT i.name FROM item_units u JOIN items i ON i.id = u.item_id"
                                    " WHERE u.barcode_key = ? AND u.item_id <> ? AND i.active = 1", (key, item_id))
            if other:
                raise UnitError(f"Barcode {barcode} is already used by {other['name']}")
            if key in seen_codes:
                raise UnitError(f"Barcode {barcode} is given to two units")
            seen_codes.add(key)
        clean.append((code, factor, int(r.get("price") or 0), barcode or None))
    if buy_unit and buy_unit.lower() not in seen:
        raise UnitError(f"The receiving unit {buy_unit} is not one of the item's units")
    with db.tx():
        db.exec("DELETE FROM item_units WHERE item_id = ?", (item_id,))
        for i, (code, factor, price, barcode) in enumerate(clean):
            db.exec("INSERT INTO item_units(item_id, unit, factor, price, barcode, sort) VALUES(?,?,?,?,?,?)",
                    (item_id, code, factor, price, barcode, (i + 1) * 10))
        if buy_unit and buy_unit.lower() != item["unit"].lower():
            factor = next(f for c, f, _p, _b in clean if c.lower() == buy_unit.lower())
            db.exec("UPDATE items SET purchase_unit = ?, purchase_factor = ?, updated_at = ? WHERE id = ?",
                    (buy_unit, factor, now_str(), item_id))
        else:
            db.exec("UPDATE items SET purchase_unit = NULL, purchase_factor = 1, updated_at = ? WHERE id = ?",
                    (now_str(), item_id))


def describe(db: Database, item) -> str:
    """'6pk = 6 can, case = 24 can' for lists and hints."""
    extra = [u for u in units_for(db, item, selling=True) if not u.base]
    return ", ".join(f"{u.code} = {u.factor:g} {item['unit']}" for u in extra)
