"""Several stations at once, and network trouble while saving.

The store runs 4 stations with 2-3 selling at the same moment. These tests run 4 real processes, each its own
station with its own drawer and its own database connection, all selling the SAME items at full speed, and
then check that nothing was lost or doubled: stock, receipt numbers, drawers, ledger and audit chain."""
from __future__ import annotations

import multiprocessing as mp
import random

import psycopg
import pytest

from helpers import books_ok, item, receive


def _station_worker(conninfo, station, owner_id, item_ids, n_sales, seed, out_q):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ownpos.core.sales import Cart
    from ownpos.core.services import Services
    from ownpos.core.stations import register
    rnd = random.Random(seed)
    s = Services(conninfo, station_id=station)
    register(s.db, station)
    shift = s.shifts.open(owner_id, 10000)
    sold = {i: 0.0 for i in item_ids}
    returned = {i: 0.0 for i in item_ids}
    cash = 0
    receipts = []
    for k in range(n_sales):
        c = Cart()
        for iid in rnd.sample(item_ids, rnd.randint(1, len(item_ids))):
            unit = rnd.choice([None, None, "case"]) if iid == item_ids[0] else None
            qty = rnd.randint(1, 3)
            ln = s.sales.make_line(s.inventory.item(iid), qty, 1, unit)
            c.lines.append(ln)
            sold[iid] += ln.base_qty
        if rnd.random() < 0.2:
            s.sales.apply_sale_discount(c, 10, "Employee 10%")
        total = s.sales.totals(c)["total"]
        sid = s.sales.finalize(c, [{"method": "CASH", "tendered": total}], owner_id, shift)
        cash += total
        receipts.append(sid)
        if k % 10 == 9:                       # every 10th receipt gets one line returned
            line = s.db.one("SELECT * FROM sale_lines WHERE sale_id = ? ORDER BY line_no LIMIT 1", (sid,))
            cn = s.sales.credit_note(sid, [{"line_id": line["id"], "qty": 1}], "CASH", owner_id, shift, "test")
            returned[line["item_id"]] += line["unit_factor"]
            cash -= s.db.val("SELECT total FROM sales WHERE id = ?", (cn,))
    z = s.shifts.close(owner_id, 10000 + cash)
    out_q.put({"station": station, "sold": sold, "returned": returned, "over_short": z["over_short"],
               "expected": z["expected_cash"], "cash": cash, "receipts": len(receipts)})
    s.close()


def test_four_stations_selling_the_same_items(s, conninfo):
    items = [item(s, name=f"Item {i}", price=random.Random(i).randint(199, 4999)) for i in range(4)]
    from ownpos.core.units import save_units
    save_units(s.db, items[0], [{"unit": "case", "factor": 24, "price": 2299}])
    for iid in items:
        receive(s, iid, 1000, 1000 if iid != items[0] else 75)
    before = {iid: s.inventory.item(iid)["on_hand"] for iid in items}
    n = 60
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    procs = [ctx.Process(target=_station_worker, args=(conninfo, st, s.owner.id, items, n, st * 7, q))
             for st in (1, 2, 3, 4)]
    for p in procs:
        p.start()
    results = [q.get(timeout=300) for _ in procs]
    for p in procs:
        p.join(timeout=60)
        assert p.exitcode == 0
    # every drawer balanced to the cent
    for r in results:
        assert r["over_short"] == 0, r
    # stock: exactly what was sold and returned, no lost update
    for iid in items:
        sold = sum(r["sold"][iid] for r in results)
        back = sum(r["returned"][iid] for r in results)
        assert s.inventory.item(iid)["on_hand"] == pytest.approx(before[iid] - sold + back, abs=1e-6)
    # receipt numbers: unique and gap-free across all stations
    nos = sorted(int(x[0]) for x in s.db.all("SELECT receipt_no FROM sales"))
    assert nos == list(range(1, len(nos) + 1))
    assert len(nos) == 4 * n + 4 * (n // 10)
    # the stations really overlapped: in save order, the station changes many times
    order = [r[0] for r in s.db.all("SELECT station_id FROM sales ORDER BY id")]
    switches = sum(1 for a, b in zip(order, order[1:]) if a != b)
    assert switches > 20, switches
    from ownpos.core.auth import verify_audit_chain
    assert verify_audit_chain(s.db) == (True, None)
    books_ok(s)


# ---------------------------------------------------------------- network trouble while saving
def _cart(s, iid):
    from ownpos.core.sales import Cart
    c = Cart()
    c.lines.append(s.sales.make_line(s.inventory.item(iid), 1))
    return c


def test_connection_lost_after_commit_is_not_charged_twice(s, monkeypatch):
    """The server saved the sale but the 'saved' reply never arrived (cable pulled at that instant)."""
    from ownpos.core.db import CommitUnknown
    iid = item(s, price=1000)
    sh = s.shifts.open(s.owner.id, 0)
    c = _cart(s, iid)
    real_execute = s.db.conn.execute

    def flaky(sql, *a, **k):
        if isinstance(sql, str) and sql == "COMMIT":
            real_execute(sql, *a, **k)
            raise psycopg.OperationalError("server closed the connection unexpectedly")
        return real_execute(sql, *a, **k)
    monkeypatch.setattr(s.db.conn, "execute", flaky)
    with pytest.raises(CommitUnknown):
        s.sales.finalize(c, [{"method": "CASH", "tendered": 1070}], s.owner.id, sh)
    monkeypatch.setattr(s.db.conn, "execute", real_execute)
    s.db.reconnect()
    first = s.sales.find_by_key(c.sale_key)
    assert first is not None                                    # it WAS saved
    again = s.sales.finalize(c, [{"method": "CASH", "tendered": 1070}], s.owner.id, sh)
    assert again == first["id"]
    assert s.db.val("SELECT COUNT(*) FROM sales") == 1


def test_connection_lost_before_commit_saves_nothing(s, conninfo):
    """The connection dies in the middle of saving: the server throws the half-done work away."""
    from ownpos.core.db import ServerUnavailable
    iid = item(s, price=1000)
    sh = s.shifts.open(s.owner.id, 0)
    c = _cart(s, iid)
    pid = s.db.val("SELECT pg_backend_pid()")
    orig_issue = s.inventory.issue

    def kill_then_issue(*a, **k):
        with psycopg.connect(**{k2: v for k2, v in conninfo.items()}, autocommit=True) as other:
            other.execute("SELECT pg_terminate_backend(%s)", (pid,))
        return orig_issue(*a, **k)
    s.inventory.issue = kill_then_issue
    with pytest.raises(ServerUnavailable):
        s.sales.finalize(c, [{"method": "CASH", "tendered": 1070}], s.owner.id, sh)
    s.inventory.issue = orig_issue
    s.db.reconnect()
    assert s.sales.find_by_key(c.sale_key) is None
    assert s.db.val("SELECT COUNT(*) FROM sales") == 0
    assert s.inventory.item(iid)["on_hand"] == 0
    sid = s.sales.finalize(c, [{"method": "CASH", "tendered": 1070}], s.owner.id, sh)
    assert s.db.val("SELECT COUNT(*) FROM sales") == 1 and sid
    books_ok(s)
