"""Screen load test: how long each screen takes to open with 20,000 items and a month of receipts.

    python tools/ui_load_test.py "host=127.0.0.1 port=5432 user=postgres password=..."

Creates (and afterwards drops) a database called ownpos_uiload. Refuses to run if it already exists."""
from __future__ import annotations

import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import psycopg  # noqa: E402

DB = "ownpos_uiload"


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else "host=127.0.0.1 port=55432 user=postgres"
    with psycopg.connect(base + " dbname=postgres", autocommit=True) as c:
        if c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (DB,)).fetchone():
            sys.exit(f"{DB} already exists; drop it first (it is only ever a test database)")
        c.execute(f"CREATE DATABASE {DB}")
    try:
        run(base)
    finally:
        with psycopg.connect(base + " dbname=postgres", autocommit=True) as c:
            c.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")


def run(base):
    params = dict(p.split("=", 1) for p in base.split())
    params["dbname"] = DB
    from ownpos.core.barcode import upc_check_digit
    from ownpos.core.ledger import Line
    from ownpos.core.sales import Cart
    from ownpos.core.services import Services
    from ownpos.core.stations import register
    s = Services(params, station_id=1)
    s.conninfo = params
    register(s.db, 1)
    uid = s.auth.create_user("owner", "Owner", "owner", password="loadtest-pass", pin="9999")
    depts = [r[0] for r in s.db.all("SELECT id FROM departments")]
    rnd = random.Random(1)
    rows = []
    for i in range(20000):
        body = f"2{i + 1:010d}"
        rows.append((str(10000 + i), body + str(upc_check_digit(body)),
                     f"Item {i:05d} {rnd.choice(['Vodka', 'Wine', 'Lager', 'Chips', 'Cups'])}",
                     rnd.choice(depts), "ea", rnd.randint(199, 5999), "IN7", 1000.0, 400.0, 400.0))
    s.db.executemany("INSERT INTO items(item_no, upc, name, department_id, unit, price1, tax_code, on_hand,"
                     " cost_avg, last_cost) VALUES(?,?,?,?,?,?,?,?,?,?)", rows)
    value = sum(round(r[7] * r[8]) for r in rows)
    s.ledger.post(s.db.val("SELECT to_char(current_date, 'YYYY-MM-DD')"), "OS", None, "LOAD", "Load test stock",
                  [Line("1300", debit=value), Line("3100", credit=value)], uid)
    shift = s.shifts.open(uid, 10000)
    ids = [r[0] for r in s.db.all("SELECT id FROM items")]
    for _ in range(3000):
        c = Cart()
        for _ in range(rnd.choice([1, 2, 3])):
            c.lines.append(s.sales.make_line(s.inventory.item(rnd.choice(ids)), 1))
        s.sales.finalize(c, [{"method": "CASH", "tendered": s.sales.totals(c)["total"]}], uid, shift)
    print("store: 20,000 items, 3,000 receipts")
    from PySide6.QtWidgets import QApplication
    from ownpos.ui.app import make_app
    from ownpos.ui.main_window import NAV, MainWindow
    make_app(["load"])
    win = MainWindow(s, s.auth.get(uid), can_sell=True)
    for _g, _h, entries in NAV:
        for key, _label in entries:
            for attempt in ("first open", "again"):
                a = time.perf_counter()
                win.go(key)
                QApplication.processEvents()
                print(f"{key:20s} {attempt:10s} {(time.perf_counter() - a) * 1000:8.0f} ms")
    page = win.pages["register"]
    a = time.perf_counter()
    page._suggest_stamp = None
    page.refresh_suggestions()
    print(f"register search list rebuild {(time.perf_counter() - a) * 1000:.0f} ms")
    win.close()
    s.close()


if __name__ == "__main__":
    main()
