"""Return to vendor (return voucher) and paying the Indiana sales tax return."""
from __future__ import annotations

import pytest

from ownpos.core.purchasing import PurchaseError
from tests_pg.helpers import beer_with_packs, books_ok, item, receive, sell, vendor


def test_return_a_case_for_credit_on_account(s):
    iid = beer_with_packs(s)
    v = vendor(s)
    s.purchasing.receive(v, [{"item_id": iid, "qty": 2, "unit": "case", "unit_cost": 1800}], s.owner.id, "INV-1")
    assert s.purchasing.vendor_balance(v) == 3600
    on_hand = s.db.val("SELECT on_hand FROM items WHERE id = ?", (iid,))
    assert on_hand == 48
    pid = s.purchasing.vendor_return(v, [{"item_id": iid, "qty": 1, "unit": "case", "unit_cost": 1800}], s.owner.id,
                                     "RMA-7")
    assert s.db.val("SELECT on_hand FROM items WHERE id = ?", (iid,)) == 24          # one stock count, at once
    assert s.purchasing.vendor_balance(v) == 1800                                   # credit on what we owe
    assert s.db.val("SELECT kind FROM purchases WHERE id = ?", (pid,)) == "vendor_return"
    bills = s.purchasing.open_bills(v)
    assert sum(b["open"] for b in bills) == 1800                                    # return applied to the bill
    books_ok(s)


def test_return_refunded_in_cash_needs_open_drawer_and_shows_on_z(s):
    iid = item(s, "Wine 750ML", 1499)
    v = vendor(s, "Wine Co")
    receive(s, iid, 12, 800, vendor_id=v)
    from ownpos.core.ledger import DrawerClosed
    with pytest.raises(DrawerClosed):
        s.purchasing.vendor_return(v, [{"item_id": iid, "qty": 2, "unit_cost": 800}], s.owner.id,
                                   refund_account="1000")
    shift = s.shifts.open(s.owner.id, 10000)
    s.purchasing.vendor_return(v, [{"item_id": iid, "qty": 2, "unit_cost": 800}], s.owner.id, refund_account="1000")
    summ = s.shifts.summary(shift)
    line = next(x for x in summ["drawer_lines"] if x["key"] == "vendor_refunds_cash")
    assert line["amount"] == 1600
    assert summ["expected_cash"] == 10000 + 1600
    books_ok(s)


def test_cannot_return_more_than_on_hand(s):
    iid = item(s, "Rare whiskey", 9999)
    v = vendor(s, "Spirits Inc")
    receive(s, iid, 1, 6000, vendor_id=v)
    with pytest.raises(PurchaseError):
        s.purchasing.vendor_return(v, [{"item_id": iid, "qty": 2, "unit_cost": 6000}], s.owner.id)
    assert s.db.val("SELECT on_hand FROM items WHERE id = ?", (iid,)) == 1             # nothing half-saved


def test_credit_different_from_average_cost_goes_to_cost_corrections(s):
    iid = item(s, "Gin 1.75L", 2999)
    v = vendor(s, "Gin Co")
    receive(s, iid, 10, 1500, vendor_id=v)
    s.purchasing.vendor_return(v, [{"item_id": iid, "qty": 1, "unit_cost": 1400}], s.owner.id)   # credit $14 < $15
    assert s.ledger.balance("5100") == 100
    books_ok(s)


def test_pay_sales_tax_with_allowance(s):
    iid = item(s, "Vodka 750ML", 1000)
    receive(s, iid, 10, 500)
    sell(s, [(iid, 10, None)])
    owed = s.ledger.balance("2100")
    assert owed == 700
    s.accounting.pay_sales_tax(690, "1100", s.owner.id, allowance=10, period="Q4 2026", ref="CONF-1")
    assert s.ledger.balance("2100") == 0
    assert s.ledger.balance("4800") == 10
    books_ok(s)


def test_selling_more_than_on_hand_can_be_switched_off(s):
    from ownpos.core.sales import SaleError
    iid = item(s, "Last bottle", 1000)
    receive(s, iid, 1, 500)
    s.db.set_setting("allow_negative_stock", False)
    with pytest.raises(SaleError):
        sell(s, [(iid, 2, None)])
    assert s.db.val("SELECT COUNT(*) FROM sales") == 0          # nothing half-saved
    sell(s, [(iid, 1, None)])
    s.db.set_setting("allow_negative_stock", True)
    sell(s, [(iid, 1, None)])                                    # the default: allowed, on hand goes to -1
    assert s.db.val("SELECT on_hand FROM items WHERE id = ?", (iid,)) == -1
