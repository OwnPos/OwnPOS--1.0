"""Load test: a store with 20,000 items and tens of thousands of receipts. Prints timings.

    python tools/load_test.py "host=127.0.0.1 port=5432 user=postgres password=..." [sales]

Creates (and afterwards drops) a database called ownpos_loadtest on that server. Never point it at the
store's real database name: it refuses to run on a database that already exists."""
from __future__ import annotations

import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg  # noqa: E402

DB = "ownpos_loadtest"


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "host=127.0.0.1 port=55432 user=postgres"
    n_sales = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
    with psycopg.connect(base + " dbname=postgres", autocommit=True) as c:
        if c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB,)).fetchone():
            sys.exit(f"{DB} already exists; drop it first (it is only ever a test database)")
        c.execute(f"CREATE DATABASE {DB}")
    params = dict(p.split("=", 1) for p in base.split())
    params["dbname"] = DB
    from ownpos.core.barcode import upc_check_digit
    from ownpos.core.sales import Cart
    from ownpos.core.services import Services
    from ownpos.core.stations import register
    s = Services(params, station_id=1)
    register(s.db, 1)
    uid = s.auth.create_user("owner", "Owner", "owner", password="loadtest-pass", pin="9999")
    depts = [r[0] for r in s.db.all("SELECT id FROM departments")]
    rnd = random.Random(1)
    t = time.time()
    rows = []
    for i in range(20000):
        body = f"2{i + 1:010d}"
        rows.append((str(10000 + i), body + str(upc_check_digit(body)), f"Item {i:05d} {rnd.choice(['Vodka', 'Wine', 'Lager', 'Chips', 'Cups'])}",
                     rnd.choice(depts), "ea", rnd.randint(199, 5999), "IN7", 1000.0, 400.0, 400.0))
    s.db.executemany("INSERT INTO items(item_no, upc, name, department_id, unit, price1, tax_code, on_hand, cost_avg,"
                     " last_cost) VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    # matching opening value in the books so the ledger agrees with the stock list
    from ownpos.core.ledger import Line
    value = sum(round(r[7] * r[8]) for r in rows)
    s.ledger.post(s.db.val("SELECT to_char(current_date, 'YYYY-MM-DD')"), "OS", None, "LOAD", "Load test stock",
                  [Line("1300", debit=value), Line("3100", credit=value)], uid)
    print(f"20,000 items inserted in {time.time() - t:.1f} s")
    upcs = [r[1] for r in rows]
    for label, fn in (("scan exact UPC", lambda: s.sales.find_item(rnd.choice(upcs))),
                      ("scan UPC without leading digit", lambda: s.sales.find_item(rnd.choice(upcs)[1:])),
                      ("type item #", lambda: s.sales.find_item(str(10000 + rnd.randint(0, 19999)))),
                      ("search 2 words", lambda: s.sales.search_items(f"{rnd.randint(0, 199):03d} vodka"))):
        times = []
        for _ in range(300):
            a = time.perf_counter()
            fn()
            times.append((time.perf_counter() - a) * 1000)
        print(f"{label:32s} median {statistics.median(times):6.2f} ms   95% {sorted(times)[284]:6.2f} ms")
    shift = s.shifts.open(uid, 10000)
    ids = [r[0] for r in s.db.all("SELECT id FROM items")]
    times = []
    t = time.time()
    for k in range(n_sales):
        c = Cart()
        for _ in range(rnd.choice([1, 1, 2, 3])):
            c.lines.append(s.sales.make_line(s.inventory.item(rnd.choice(ids)), 1))
        total = s.sales.totals(c)["total"]
        a = time.perf_counter()
        s.sales.finalize(c, [{"method": "CASH", "tendered": total}], uid, shift)
        times.append((time.perf_counter() - a) * 1000)
    print(f"{n_sales} receipts saved in {time.time() - t:.0f} s; save median {statistics.median(times):.1f} ms,"
          f" 99% {sorted(times)[int(len(times) * 0.99)]:.1f} ms")
    from ownpos.core.reports import REGISTRY, run_report
    today = s.db.val("SELECT to_char(current_date, 'YYYY-MM-DD')")
    slow = []
    for key in REGISTRY:
        a = time.perf_counter()
        run_report(s.db, key, {"date_from": "2020-01-01", "date_to": today})
        slow.append(((time.perf_counter() - a), key))
    slow.sort(reverse=True)
    print("slowest reports:", ", ".join(f"{k} {t:.2f}s" for t, k in slow[:6]))
    print("ledger balanced:", s.ledger.is_balanced())
    s.close()
    with psycopg.connect(base + " dbname=postgres", autocommit=True) as c:
        c.execute(f"DROP DATABASE {DB}")


if __name__ == "__main__":
    main()
