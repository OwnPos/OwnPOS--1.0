"""Time clock, licences, smart import, backups, pricing and stock corrections."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from helpers import beer_with_packs, books_ok, item, receive, sell


# ---------------------------------------------------------------- time clock
def test_clock_in_out_and_fix(s):
    from ownpos.core.timeclock import TimeClockError
    uid = s.auth.create_user("amy", "Amy Cashier", "cashier", pin="1111", pay_rate=1200)
    s.timeclock.clock_in(uid)
    with pytest.raises(TimeClockError):
        s.timeclock.clock_in(uid)
    s.timeclock.clock_out(uid)
    with pytest.raises(TimeClockError):
        s.timeclock.clock_out(uid)
    cashier = s.auth.get(uid)
    with pytest.raises(TimeClockError):                    # cashier cannot fix punches
        s.timeclock.save_entry(uid, "2026-10-01 09:00", "2026-10-01 17:00", "missed", cashier)
    with pytest.raises(TimeClockError):                    # reason required
        s.timeclock.save_entry(uid, "2026-10-01 09:00", "2026-10-01 17:00", "", s.owner)
    tid = s.timeclock.save_entry(uid, "2026-10-01 09:00", "2026-10-01 17:00", "Forgot to clock in", s.owner)
    with pytest.raises(TimeClockError):                    # overlap
        s.timeclock.save_entry(uid, "2026-10-01 16:00", "2026-10-01 18:00", "x", s.owner)
    assert s.db.val("SELECT edit_reason FROM time_entries WHERE id = ?", (tid,)) == "Forgot to clock in"


def test_overtime_over_40_hours_in_a_workweek(s):
    uid = s.auth.create_user("bob", "Bob Clerk", "cashier", pin="2222", pay_rate=1500)
    # Sunday 2026-10-04 starts the workweek (default). 5 x 9 h = 45 h -> 40 regular + 5 overtime
    for d in range(5):
        day = date(2026, 10, 4) + timedelta(days=d)
        s.timeclock.save_entry(uid, f"{day} 08:00", f"{day} 17:00", "test", s.owner)
    rows = s.timeclock.payroll("2026-10-04", "2026-10-10", with_pay=True)
    r = next(x for x in rows if x["employee"] == "Bob Clerk")
    assert (r["hours"], r["regular"], r["overtime"]) == (45.0, 40.0, 5.0)
    assert r["gross_pay"] == 40 * 1500 + 5 * 1500 * 1.5           # $712.50
    assert r["week_start"] == "2026-10-04"


def test_shift_past_midnight_is_split_at_the_workweek_boundary(s):
    """Saturday 22:00 -> Sunday 03:00 with the week starting Sunday: 2 h in one week, 3 h in the next."""
    uid = s.auth.create_user("night", "Night Clerk", "cashier", pin="3333", pay_rate=1000)
    s.timeclock.save_entry(uid, "2026-10-10 22:00", "2026-10-11 03:00", "test", s.owner)
    rows = [r for r in s.timeclock.payroll("2026-10-04", "2026-10-17") if r["employee"] == "Night Clerk"]
    by_week = {r["week_start"]: r["hours"] for r in rows}
    assert by_week == {"2026-10-04": 2.0, "2026-10-11": 3.0}
    assert sum(r["punches"] for r in rows) == 1


# ---------------------------------------------------------------- licence
@pytest.fixture
def signing(monkeypatch):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from ownpos.core import licence
    key = Ed25519PrivateKey.generate()
    pub = licence._b64e(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    monkeypatch.setattr(licence, "public_key_b64", lambda: pub)

    def make(store_uid, stations=4, expires=None):
        import json
        payload = {"product": "OwnPOS", "v": 1, "serial": "T-1", "licensee": "Test Liquors", "store_uid": store_uid,
                   "stations": stations, "expires": expires, "issued": "2026-10-16", "features": ["all"]}
        signed = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return licence.pack(payload, key.sign(signed))
    return make


def test_licence_states(s, signing):
    from ownpos.core import licence
    assert licence.status(s.db).state == "missing"
    uid = licence.store_uid(s.db)
    text = signing(uid, stations=2)
    licence.install(s.db, text, s.owner.id)
    st = licence.status(s.db)
    assert st.state == "ok" and st.can_sell
    # a changed character breaks the signature
    body = text.splitlines()[1]
    bad = text.replace(body, body[:10] + ("A" if body[10] != "A" else "B") + body[11:])
    with pytest.raises(licence.LicenceError):
        licence.verify_text(bad)
    # another store's licence is refused
    with pytest.raises(licence.LicenceError):
        licence.install(s.db, signing("someotherstore"), s.owner.id)
    # expiry, grace, then selling pauses
    today = date(2026, 10, 16)
    licence.install(s.db, signing(uid, expires="2026-10-20"), s.owner.id)
    assert licence.status(s.db, today).state == "expiring"
    assert licence.status(s.db, date(2026, 10, 25)).state == "grace"
    exp = licence.status(s.db, date(2026, 10, 28))
    assert exp.state == "expired" and not exp.can_sell


def test_station_limit(s, signing):
    from ownpos.core import licence
    from ownpos.core.stations import register
    licence.install(s.db, signing(licence.store_uid(s.db), stations=2), s.owner.id)
    assert register(s.db, 1)["can_sell"]
    assert register(s.db, 2)["can_sell"]
    third = register(s.db, 3)
    assert not third["can_sell"] and "2 station" in third["message"]
    assert register(s.db, 1)["can_sell"]                    # the licensed ones keep working


# ---------------------------------------------------------------- smart import
def test_import_quickbooks_style_item_export(s, tmp_path):
    from openpyxl import Workbook
    from ownpos.core.excel import commit_import, read_header, read_import
    from ownpos.core.lists import get_entity
    wb = Workbook()
    ws = wb.active
    ws.append(["Item List"])                                # title rows above the header, like a QB export
    ws.append(["Delock's Liquors - exported 10/15/2026"])
    ws.append([])
    ws.append(["Item Number", "Item Name", "Size", "Department Name", "Vendor Name", "UPC", "Regular Price",
               "Average Unit Cost", "Qty 1", "Tax Code", "Reorder Point 1"])
    ws.append([101, "TITOS HANDMADE VODKA", "750ML", "Liquor", "Southern Glazer's", "'619947000020", 24.99, 16.5, 12,
               "Tax", 6])
    ws.append([102, "BUD LIGHT 12PK CAN", "12OZ", "Beer", "Lake Beverage", 18200000164, 13.49, 9.8, 30, "Tax", 10])
    ws.append([103, "PLASTIC CUPS 50CT", "", "Party Supplies", "", "", 4.99, 2.1, 0, "Tax", 0])
    ws.append([104, "BAD UPC ROW", "", "Liquor", "", "1.23457E+11", 9.99, 5, 1, "Tax", 0])   # what a CSV round-trip leaves
    path = tmp_path / "items.xlsx"
    wb.save(path)
    ent = get_entity("items")
    hi, headings, samples, mapping = read_header(path, ent)
    assert hi == 3 and {"item_no", "name", "upc", "price1", "department_id", "_open_qty", "_open_cost"} <= set(mapping)
    rows = read_import(path, s.lists, ent)
    assert [r.action for r in rows] == ["add", "add", "add", "skip"]
    assert "scientific" in " ".join(rows[3].errors) or "rounded" in " ".join(rows[3].errors)
    res = commit_import(s.lists, ent, rows, s.owner.id, services=s)
    assert res["added"] == 3 and res["failed"] == 0
    tito = s.db.one("SELECT * FROM items WHERE item_no = '101'")
    assert tito["price1"] == 2499 and tito["on_hand"] == 12 and abs(tito["cost_avg"] - 1650) < 1e-9
    assert tito["upc"] == "619947000020"
    bud = s.db.one("SELECT * FROM items WHERE item_no = '102'")
    assert bud["upc"] == "18200000164"
    assert s.sales.find_item("018200000164")[0]["id"] == bud["id"]      # register still finds it
    assert s.db.val("SELECT COUNT(*) FROM departments WHERE lower(name) = 'party supplies'") == 1   # matched the existing one
    assert s.db.val("SELECT COUNT(*) FROM vendors") == 2
    # importing the same file again updates instead of duplicating, and does not add stock twice
    rows2 = read_import(path, s.lists, ent)
    assert [r.action for r in rows2][:3] == ["update", "update", "update"]
    commit_import(s.lists, ent, rows2, s.owner.id, services=s)
    assert s.db.val("SELECT COUNT(*) FROM items") == 3
    assert s.db.one("SELECT on_hand FROM items WHERE item_no = '101'")[0] == 12
    books_ok(s)


def test_import_csv_customers_and_vendors(s, tmp_path):
    from ownpos.core.excel import commit_import, read_import
    from ownpos.core.lists import get_entity
    p = tmp_path / "customers.csv"
    p.write_text("Customer Name,Phone,Email,Charge Limit,Balance\nJoe's Bar,219-555-0100,joe@example.com,500,120.50\n"
                 "Ann Smith,219-555-0101,,0,\n", encoding="utf-8")
    ent = get_entity("customers")
    rows = read_import(p, s.lists, ent)
    res = commit_import(s.lists, ent, rows, s.owner.id, services=s)
    assert res["added"] == 2 and res["openings"] == 1
    joe = s.db.one("SELECT * FROM customers WHERE name = ?", ("Joe's Bar",))
    assert joe["credit_limit"] == 50000
    assert s.sales.customer_balance(joe["id"]) == 12050
    books_ok(s)


# ---------------------------------------------------------------- pricing, stock corrections
def test_bulk_price_change_with_99_endings(s):
    a = item(s, name="A", price=1000)
    b = item(s, name="B", price=2050)
    prev = s.pricing.preview([a, b], "price1", "pct", 10, rounding=99)
    assert [p["new"] for p in prev] == [1199, 2299]
    assert s.pricing.apply(prev, "price1", s.owner.id, "Distributor increase") == 2
    assert s.db.val("SELECT COUNT(*) FROM price_changes") == 2


def test_waste_count_and_cost_correction(s):
    a = item(s, price=2000, unit="btl")
    receive(s, a, 10, 1000)
    s.stock.waste([(a, 1)], "Broken bottle", s.owner.id)
    s.stock.count({a: 7}, s.owner.id)                       # 9 -> 7
    assert s.inventory.item(a)["on_hand"] == 7
    s.stock.correct_cost(a, 1100, "Invoice price was wrong", s.owner)
    assert s.inventory.item(a)["cost_avg"] == 1100
    assert s.ledger.balance("1300") == 7 * 1100
    books_ok(s)


def test_backup_and_restore_into_new_database(s, conninfo, tmp_path):
    from ownpos.core.backup import backup, restore_to_new_database
    a = beer_with_packs(s)
    receive(s, a, 2, 1800, unit="case")
    sell(s, [(a, 1, "case")])
    s.db.set_setting("backup_folder", str(tmp_path))
    path = backup(s.db, conninfo, "test")
    assert path.exists() and path.stat().st_size > 1000
    new = restore_to_new_database(s.db, conninfo, path)
    import psycopg
    with psycopg.connect(**dict(conninfo, dbname=new)) as c:
        assert c.execute("SELECT COUNT(*) FROM sales").fetchone()[0] == 1
        assert c.execute("SELECT on_hand FROM items WHERE id = %s", (a,)).fetchone()[0] == 24
    with psycopg.connect(**dict(conninfo, dbname="postgres"), autocommit=True) as c:
        c.execute(f'DROP DATABASE "{new}"')


def test_agents_run_without_errors(s):
    a = beer_with_packs(s)
    s.db.exec("UPDATE items SET reorder_point = 50 WHERE id = ?", (a,))
    receive(s, a, 1, 1800, unit="case")
    sell(s, [(a, 1, None)])
    s.conninfo = None
    for name in s.agents.agents:
        if name == "backup":
            continue
        out = s.agents.run(name, "manual")
        assert not out.startswith("Error"), (name, out)
    assert s.db.val("SELECT COUNT(*) FROM purchase_orders WHERE created_by_agent = 1") == 1
