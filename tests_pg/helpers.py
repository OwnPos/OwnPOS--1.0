"""Small helpers shared by the tests: make items, vendors, receive stock, ring up sales."""
from __future__ import annotations

from ownpos.core.lists import get_entity
from ownpos.core.sales import Cart
from ownpos.core.units import save_units


def dept(s, name="Beer"):
    return s.db.val("SELECT id FROM departments WHERE name = ?", (name,))


def item(s, name="Bud Light 12oz can", price=129, upc=None, department="Beer", unit="can", tax="IN7", **extra):
    values = {"name": name, "price1": price, "department_id": dept(s, department), "unit": unit, "tax_code": tax}
    if upc:
        values["upc"] = upc
    values.update(extra)
    return s.lists.save(get_entity("items"), values, s.owner.id)


def beer_with_packs(s):
    iid = item(s, upc="018200000164")
    save_units(s.db, iid, [{"unit": "6pk", "factor": 6, "price": 699, "barcode": "018200000171"},
                           {"unit": "case", "factor": 24, "price": 2299, "barcode": "018200000188"}])
    return iid


def vendor(s, name="Southern Glazer's", terms=30):
    return s.lists.save(get_entity("vendors"), {"name": name, "terms_days": terms}, s.owner.id)


def receive(s, item_id, qty, cost, unit=None, vendor_id=None, paid=None):
    vid = vendor_id or vendor(s, f"Vendor {item_id}-{qty}-{cost}")
    return s.purchasing.receive(vid if paid is None else None, [{"item_id": item_id, "qty": qty, "unit": unit,
                                                                "unit_cost": cost}], s.owner.id,
                                paid_account=paid)


def sell(s, lines, tenders=None, shift=None, discount_pct=None, reason="", customer_id=None):
    """lines: [(item_id, qty, unit or None)]; tenders default = exact cash."""
    c = Cart()
    if customer_id:
        c.customer_id = customer_id
    for iid, qty, unit in lines:
        c.lines.append(s.sales.make_line(s.inventory.item(iid), qty, 1, unit))
    if discount_pct is not None:
        s.sales.apply_sale_discount(c, discount_pct, reason)
    total = s.sales.totals(c)["total"]
    if shift is None:
        cur = s.shifts.current()
        shift = cur["id"] if cur else s.shifts.open(s.owner.id, 10000)
    sid = s.sales.finalize(c, tenders or [{"method": "CASH", "tendered": total}], s.owner.id, shift)
    return sid, c


def books_ok(s, tolerance_cents: int | None = None):
    """Ledger balanced, balance sheet balanced, stock value = Inventory account.

    Each stock movement is valued to the cent while the average cost keeps its fractions, so the stock list
    (on hand x average cost, rounded once per item) and the account (sum of rounded movements) may differ by
    a few cents per item over many movements. Default tolerance: 1 cent per stock movement."""
    assert s.ledger.is_balanced()
    from ownpos.core.db import today_str
    assert s.ledger.balance_sheet(today_str())["balanced"]
    stock = sum(v["value"] for v in s.inventory.valuation())
    moves = s.db.val("SELECT COUNT(*) FROM stock_moves", default=0)
    tol = moves if tolerance_cents is None else tolerance_cents
    assert abs(s.ledger.balance("1300") - stock) <= tol, (s.ledger.balance("1300"), stock, tol)
