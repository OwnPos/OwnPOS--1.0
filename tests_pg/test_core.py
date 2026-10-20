"""Core money, tax, stock and receipt rules on a real PostgreSQL database."""
from __future__ import annotations

import pytest

from helpers import beer_with_packs, books_ok, item, receive, sell, vendor
from ownpos.core.db import CommitUnknown, Row
from ownpos.core.money import fmt_money, to_cents
from ownpos.core.sales import Cart, SaleError
from ownpos.core.tax import calc_receipt, split_by_weight, tax_on


# ---------------------------------------------------------------- money and tax (no database)
def test_money_parsing_and_format():
    assert to_cents("$1,234.56") == 123456
    assert to_cents("19.995") == 2000          # half-up
    assert to_cents("(12.50)") == -1250
    assert to_cents(3) == 300
    assert fmt_money(123456, "$") == "$1,234.56"
    assert fmt_money(-1250, "$") == "-$12.50"


def test_indiana_tax_rounding_half_up():
    # IC 6-2.5-2-2(b): third decimal greater than four rounds up
    assert tax_on(50, 7.0) == 4            # 3.5 cents -> 4
    assert tax_on(21, 7.0) == 1            # 1.47 -> 1
    assert tax_on(2557, 7.0) == 179        # 178.99 -> 179
    assert tax_on(1000, 7.0) == 70


def test_invoice_basis_tax_and_split():
    # three 0.50 items: per-item rounding would charge 4+4+4 = 12 cents; invoice basis charges round(10.5) = 11
    lines, t = calc_receipt([(1, 50, 0, 7.0), (1, 50, 0, 7.0), (1, 50, 0, 7.0)])
    assert t["tax"] == 11
    assert sum(x.tax for x in lines) == 11
    assert t["total"] == 161
    assert split_by_weight(11, [50, 50, 50]) == [4, 4, 3]
    assert sum(split_by_weight(-7, [3, 3, 1])) == -7


def test_nontaxable_and_exempt_customer():
    lines, t = calc_receipt([(1, 1000, 0, 7.0), (1, 500, 0, 0.0)])
    assert (t["taxable"], t["nontaxable"], t["tax"], t["total"]) == (1000, 500, 70, 1570)
    _, t2 = calc_receipt([(1, 1000, 0, 7.0)], tax_exempt=True)
    assert t2["tax"] == 0 and t2["total"] == 1000


# ---------------------------------------------------------------- database layer
def test_row_works_like_sqlite_row(s):
    r = s.db.one("SELECT 1 AS a, 'x%' AS b, 2 AS c")
    assert isinstance(r, Row)
    assert r["a"] == 1 and r[1] == "x%" and dict(r) == {"a": 1, "b": "x%", "c": 2}
    a, b, c = r
    assert c == 2
    assert s.db.val("SELECT SUM(x) FROM (VALUES (CAST(5 AS BIGINT)), (6)) v(x)") == 11   # numeric -> int


def test_next_seq_needs_transaction(s):
    with pytest.raises(RuntimeError):
        s.db.next_seq("X")
    with s.db.tx():
        assert s.db.next_seq("X") == 1
        assert s.db.next_seq("X") == 2


def test_locked_records_cannot_change(s):
    iid = item(s)
    sid, _ = sell(s, [(iid, 1, None)])
    with pytest.raises(Exception):
        s.db.exec("UPDATE sales SET total = 1 WHERE id = ?", (sid,))
    with pytest.raises(Exception):
        s.db.exec("DELETE FROM sale_lines WHERE sale_id = ?", (sid,))
    with pytest.raises(Exception):
        s.db.exec("UPDATE journal_lines SET debit = debit + 1")
    with pytest.raises(Exception):
        s.db.exec("DELETE FROM audit_log")


# ---------------------------------------------------------------- items, units, lookup
def test_upc_lookup_variants(s):
    iid = beer_with_packs(s)
    assert s.sales.find_item("018200000164")[0]["id"] == iid
    assert s.sales.find_item("18200000164")[0]["id"] == iid          # Excel dropped the leading 0
    assert s.sales.find_item("01820000016")[0]["id"] == iid          # scanned without check digit
    assert s.sales.find_item("0018200000164")[0]["id"] == iid        # EAN-13 form
    item_row, unit = s.sales.find_item("018200000188")
    assert item_row["id"] == iid and unit == "case"
    assert s.sales.find_item("999999999999") is None


def test_duplicate_upc_refused(s):
    from ownpos.core.lists import ValidationError
    item(s, name="A", upc="012345678905")
    with pytest.raises(ValidationError):
        item(s, name="B", upc="012345678905")


def test_case_sells_from_the_same_stock(s):
    iid = beer_with_packs(s)
    receive(s, iid, 10, 1800, unit="case")                  # 10 cases at $18.00 = 240 cans at $0.75
    it = s.inventory.item(iid)
    assert it["on_hand"] == 240 and abs(it["cost_avg"] - 75) < 1e-9
    sell(s, [(iid, 1, "case"), (iid, 1, "6pk"), (iid, 3, None)])
    assert s.inventory.item(iid)["on_hand"] == 240 - 24 - 6 - 3
    line = s.db.one("SELECT * FROM sale_lines WHERE unit = 'case'")
    assert line["price"] == 2299 and line["unit_factor"] == 24 and line["cost_total"] == 1800
    books_ok(s)


def test_receiving_shows_on_hand_immediately_and_averages_cost(s):
    iid = item(s, name="Tito's 750ML", price=2499, department="Liquor", unit="btl")
    receive(s, iid, 12, 1500)
    receive(s, iid, 6, 1800)
    it = s.inventory.item(iid)
    assert it["on_hand"] == 18
    assert abs(it["cost_avg"] - (12 * 1500 + 6 * 1800) / 18) < 1e-9
    assert it["last_cost"] == 1800
    assert s.purchasing.vendor_balance(s.db.val("SELECT vendor_id FROM purchases ORDER BY id LIMIT 1")) == 18000
    books_ok(s)


# ---------------------------------------------------------------- sales, discounts, payments
def test_sale_totals_tax_and_ledger(s):
    a = item(s, name="Wine", price=1999, department="Wine", unit="btl")
    b = item(s, name="Lime", price=449, department="Snacks")
    receive(s, a, 10, 1000)
    sid, cart = sell(s, [(a, 1, None), (b, 1, None)])
    sale = s.db.one("SELECT * FROM sales WHERE id = ?", (sid,))
    assert (sale["subtotal"], sale["taxable"], sale["tax"], sale["total"]) == (2448, 2448, 171, 2619)
    assert s.ledger.balance("2100") == 171
    assert s.ledger.balance("4000") == 2448
    books_ok(s)


def test_employee_discount_buttons(s):
    a = item(s, price=1000)
    sid, _ = sell(s, [(a, 3, None)], discount_pct=10, reason="Employee 10%")
    sale = s.db.one("SELECT * FROM sales WHERE id = ?", (sid,))
    assert sale["discount"] == 300 and sale["taxable"] == 2700 and sale["tax"] == 189
    assert sale["discount_reason"] == "Employee 10%"
    dc = s.shifts.day_closing(sale["sale_date"])
    assert dc["discount_lines"][0]["reason"] == "Employee 10%" and dc["discount_lines"][0]["amount"] == 300


def test_split_payment_change_and_card_reference(s):
    a = item(s, price=2000)
    c = Cart()
    c.lines.append(s.sales.make_line(s.inventory.item(a), 1))
    total = s.sales.totals(c)["total"]                          # 2140
    sh = s.shifts.open(s.owner.id, 10000)
    sid = s.sales.finalize(c, [{"method": "CARD", "tendered": 1000, "ref": "4242", "auth": "A1B2"},
                               {"method": "CASH", "tendered": 2000}], s.owner.id, sh)
    tenders = {t["method_code"]: t for t in s.db.all("SELECT * FROM sale_tenders WHERE sale_id = ?", (sid,))}
    assert tenders["CARD"]["amount"] == 1000 and tenders["CARD"]["ref"] == "4242"
    assert tenders["CASH"]["amount"] == total - 1000
    assert tenders["CASH"]["change"] == 2000 - (total - 1000)
    with pytest.raises(SaleError):           # change only from cash
        c2 = Cart()
        c2.lines.append(s.sales.make_line(s.inventory.item(a), 1))
        s.sales.finalize(c2, [{"method": "CARD", "tendered": 5000}], s.owner.id, sh)


def test_check_needs_number_and_house_charge_needs_customer(s):
    a = item(s, price=1000)
    sh = s.shifts.open(s.owner.id, 0)
    c = Cart()
    c.lines.append(s.sales.make_line(s.inventory.item(a), 1))
    with pytest.raises(SaleError):
        s.sales.finalize(c, [{"method": "CHECK", "tendered": 1070}], s.owner.id, sh)
    with pytest.raises(SaleError):
        s.sales.finalize(c, [{"method": "CHARGE", "tendered": 1070}], s.owner.id, sh)


def test_house_charge_limit_and_payment(s):
    from ownpos.core.lists import get_entity
    cid = s.lists.save(get_entity("customers"), {"name": "Joe's Bar", "credit_limit": 5000}, s.owner.id)
    a = item(s, price=2000)
    sid, _ = sell(s, [(a, 2, None)], tenders=[{"method": "CHARGE", "tendered": 4280}], customer_id=cid)
    assert s.sales.customer_balance(cid) == 4280
    with pytest.raises(SaleError):
        sell(s, [(a, 1, None)], tenders=[{"method": "CHARGE", "tendered": 2140}], customer_id=cid)
    s.sales.receive_payment(cid, 4280, "CHECK", s.owner.id, None, ref="1001")
    assert s.sales.customer_balance(cid) == 0


def test_sale_key_prevents_double_saving(s):
    a = item(s, price=500)
    c = Cart()
    c.lines.append(s.sales.make_line(s.inventory.item(a), 1))
    sh = s.shifts.open(s.owner.id, 0)
    t = s.sales.totals(c)["total"]
    first = s.sales.finalize(c, [{"method": "CASH", "tendered": t}], s.owner.id, sh)
    again = s.sales.finalize(c, [{"method": "CASH", "tendered": t}], s.owner.id, sh)   # retry after a "drop"
    assert first == again
    assert s.db.val("SELECT COUNT(*) FROM sales") == 1


# ---------------------------------------------------------------- returns and voids
def test_partial_returns_add_up_exactly(s):
    a = item(s, price=333)
    receive(s, a, 10, 100)
    sid, _ = sell(s, [(a, 3, None)])
    orig = s.db.one("SELECT * FROM sale_lines WHERE sale_id = ?", (sid,))
    sh = s.shifts.current()["id"]
    for _ in range(3):
        s.sales.credit_note(sid, [{"line_id": orig["id"], "qty": 1}], "CASH", s.owner.id, sh, "Changed mind")
    back = s.db.one("SELECT SUM(l.net) n, SUM(l.tax) t, SUM(l.total) tot, SUM(l.cost_total) c FROM sale_lines l"
                    " JOIN sales x ON x.id = l.sale_id WHERE x.doc_type = 'CN'")
    assert (back["n"], back["t"], back["tot"], back["c"]) == (orig["net"], orig["tax"], orig["total"], orig["cost_total"])
    assert s.inventory.item(a)["on_hand"] == 10
    with pytest.raises(SaleError):
        s.sales.credit_note(sid, [{"line_id": orig["id"], "qty": 1}], "CASH", s.owner.id, sh, "again")
    books_ok(s)


def test_void_mirrors_payments(s):
    a = item(s, price=2000)
    receive(s, a, 5, 1000)
    sid, c = sell(s, [(a, 1, None)], tenders=[{"method": "CARD", "tendered": 1000, "ref": "1111"},
                                                {"method": "CASH", "tendered": 1140}])
    cn = s.sales.cancel_bill(sid, "Rang wrong item", s.owner.id, s.shifts.current()["id"])
    refunds = {r["method_code"]: r["amount"] for r in s.db.all("SELECT * FROM sale_tenders WHERE sale_id = ?", (cn,))}
    assert refunds == {"CARD": 1000, "CASH": 1140}
    assert s.db.val("SELECT is_active FROM sales WHERE id = ?", (sid,)) == 0
    assert s.db.val("SELECT notes FROM sales WHERE id = ?", (cn,)) == "Void"
    with pytest.raises(SaleError):
        s.sales.cancel_bill(sid, "again", s.owner.id, None)
    books_ok(s)


def test_broken_return_not_restocked(s):
    a = item(s, price=1500)
    receive(s, a, 2, 900)
    sid, _ = sell(s, [(a, 1, None)])
    line = s.db.one("SELECT id FROM sale_lines WHERE sale_id = ?", (sid,))
    s.sales.credit_note(sid, [{"line_id": line["id"], "qty": 1, "restock": False}], "CASH", s.owner.id, None, "Broken")
    assert s.inventory.item(a)["on_hand"] == 1
    assert s.ledger.balance("5200") == 900
    books_ok(s)


# ---------------------------------------------------------------- drawers
def test_drawers_are_per_station(s, conninfo):
    from ownpos.core.services import Services
    from ownpos.core.shifts import ShiftError
    from ownpos.core.stations import register
    s2 = Services(conninfo, station_id=2)
    register(s2.db, 2)
    try:
        a = item(s, price=1000)
        sh1 = s.shifts.open(s.owner.id, 10000)
        sh2 = s2.shifts.open(s.owner.id, 5000)
        assert sh1 != sh2
        with pytest.raises(ShiftError):
            s.shifts.open(s.owner.id, 0)                    # station 1 already open
        sell(s, [(a, 1, None)], shift=sh1)
        c = Cart()
        c.lines.append(s2.sales.make_line(s2.inventory.item(a), 2))
        s2.sales.finalize(c, [{"method": "CASH", "tendered": 2140}], s.owner.id, sh2)
        s2.shifts.cash_move("payout", 500, s.owner.id, "Ice", "6300")
        z1 = s.shifts.close(s.owner.id, 10000 + 1070)
        assert z1["over_short"] == 0 and z1["expected_cash"] == 11070
        z2 = s2.shifts.close(s.owner.id, 5000 + 2140 - 500 - 100)          # $1 short
        assert z2["over_short"] == -100
        dc = s.shifts.day_closing(z1["shift"]["opened_at"][:10])
        assert dc["n_inv"] == 2 and dc["tax"] == 70 + 140 and dc["over_short"] == -100
        books_ok(s)
    finally:
        s2.close()


def test_hours_warning_logic():
    from datetime import datetime
    from ownpos.core.hours import is_open
    from ownpos.core.seed import INDIANA_SALE_HOURS as H
    assert is_open(H, datetime(2026, 10, 16, 22, 0))        # Friday 10 pm
    assert is_open(H, datetime(2026, 10, 17, 2, 30))        # Saturday 2:30 am (Friday's hours run to 3 am)
    assert not is_open(H, datetime(2026, 10, 17, 4, 0))     # Saturday 4 am
    assert is_open(H, datetime(2026, 10, 18, 1, 0))         # Sunday 1 am (Saturday's hours)
    assert not is_open(H, datetime(2026, 10, 18, 11, 0))    # Sunday 11 am
    assert is_open(H, datetime(2026, 10, 18, 12, 0))        # Sunday noon
    assert not is_open(H, datetime(2026, 10, 18, 20, 30))   # Sunday 8:30 pm
    assert not is_open(H, datetime(2026, 10, 19, 6, 59))    # Monday 6:59 am


def test_all_reports_run(s):
    from ownpos.core.reports import REGISTRY, run_report
    a = beer_with_packs(s)
    receive(s, a, 2, 1800, unit="case")
    sell(s, [(a, 1, "case")])
    from ownpos.core.db import today_str
    for key in REGISTRY:
        res = run_report(s.db, key, {"date_from": today_str(), "date_to": today_str()})
        assert res.columns, key
