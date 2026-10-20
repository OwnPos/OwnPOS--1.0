"""A sample liquor store for training and demonstrations ("Sample Liquor Store").

Everything here is invented: item names are generic, UPCs start with 2 (the range reserved for in-store codes,
so they can never match a real product), prices and sales are random. It is created only in a separate demo
database, never in the real store's database.

The sample uses the real program paths (receiving, sales, returns, Z-Outs, time clock), so its books are built
exactly like a real store's and every report has something to show."""
from __future__ import annotations

import random
from contextlib import contextmanager
from datetime import date, datetime, timedelta

from . import db as dbmod
from .barcode import upc_check_digit
from .lists import get_entity
from .sales import Cart
from .units import save_units

SIZES = {"Liquor": ["750ML", "1.75L", "1L", "375ML", "200ML", "50ML"], "Wine": ["750ML", "1.5L"],
         "Beer": ["12OZ", "16OZ", "24OZ"]}
LIQUOR = ["Vodka", "Silver Tequila", "Reposado Tequila", "Kentucky Bourbon", "Rye Whiskey", "Irish Whiskey",
          "Scotch Blend", "Spiced Rum", "White Rum", "London Dry Gin", "Coffee Liqueur", "Peach Schnapps",
          "Cognac VS", "Triple Sec", "Cinnamon Whisky", "Flavored Vodka"]
QUALITY = ["House", "Premium", "Reserve"]
WINES = ["Cabernet Sauvignon", "Merlot", "Pinot Noir", "Chardonnay", "Pinot Grigio", "Sauvignon Blanc", "Moscato",
         "Rose", "Prosecco", "Red Blend"]
BEERS = ["Light Lager", "Premium Lager", "Mexican Lager", "IPA", "Hard Seltzer Variety", "Wheat Ale", "Stout",
         "Craft Pale Ale", "Malt Beverage", "Ice Lager"]
OTHER = [("Mixers & soft drinks", ["Tonic Water 1L", "Club Soda 1L", "Ginger Beer 4pk", "Cola 2L", "Lime Juice 12OZ",
                                   "Margarita Mix 1.75L", "Energy Drink 16OZ", "Spring Water 24pk"]),
         ("Snacks", ["Potato Chips", "Tortilla Chips", "Salsa Jar", "Beef Jerky", "Mixed Nuts", "Pretzels",
                     "Candy Bar", "Gum"]),
         ("Drink accessories", ["Bottle Opener", "Wine Opener", "Shot Glasses 4pk", "Cocktail Shaker",
                                "Koozie", "Pour Spouts 2pk", "Ice Mold", "Wine Bag"]),
         ("Party supplies", ["Plastic Cups 50ct", "Red Cups 20ct", "Paper Plates 50ct", "Napkins 100ct",
                             "Balloons 12ct", "Party Hats 8pk", "Ice Bucket", "Table Cover"]),
         ("Ice", ["Ice Bag 10LB", "Ice Bag 20LB"]),
         ("Tobacco", ["Cigarillos 2pk", "Rolling Papers", "Lighter"])]
VENDORS = [("SAMPLE-SPIRITS", "Sample Spirits Distributor", 30), ("SAMPLE-BEER", "Sample Beer Distributor", 7),
           ("SAMPLE-WINE", "Sample Wine Company", 30), ("SAMPLE-MIX", "Sample Beverage Supply", 14),
           ("SAMPLE-SNACK", "Sample Snack Wholesale", 14), ("SAMPLE-PARTY", "Sample Party Goods", 30)]


def _upc(n: int) -> str:
    body = f"2{n:010d}"
    return body + str(upc_check_digit(body))


@contextmanager
def _at(when: datetime):
    """Run code as if the store clock showed `when` (sample history only)."""
    old = dbmod._clock["offset"]
    dbmod._clock["offset"] = (when - datetime.now()).total_seconds()
    try:
        yield
    finally:
        dbmod._clock["offset"] = old


def fill_sample(s, owner_id: int, days: int = 21, sales_per_day: int = 120, seed: int = 7) -> dict:
    """Fill an EMPTY demo database with a sample liquor store. Returns counts."""
    if s.db.val("SELECT COUNT(*) FROM items", default=0):
        raise ValueError("The sample store can only be made in an empty database")
    rnd = random.Random(seed)
    st = s.db
    own_station = st.station_id
    from .stations import register
    for sid in (1, 2):                          # the sample shifts run on stations 1 and 2
        if not st.val("SELECT 1 FROM stations WHERE id = ?", (sid,)):
            register(st, sid, f"Station {sid}")
    st.set_setting("company_name", "Sample Liquor Store")
    st.set_setting("company_address", "100 Example Ave")
    st.set_setting("company_city_state_zip", "Anytown, IN 46000")
    st.set_setting("is_demo", True)
    dept = {r["name"]: r["id"] for r in st.all("SELECT id, name FROM departments")}
    vend = {}
    for code, name, terms in VENDORS:
        vend[code] = s.lists.save(get_entity("vendors"), {"code": code, "name": name, "terms_days": terms}, owner_id)
    items = []                                          # (id, department, cost, weight)
    n = 1

    def add(name, department, size, price, cost, vendor, unit="ea", weight=1.0, packs=None, reorder=6):
        nonlocal n
        iid = s.lists.save(get_entity("items"), {
            "name": name, "size": size, "department_id": dept[department], "unit": unit, "price1": price,
            "upc": _upc(n), "vendor_id": vend[vendor], "reorder_point": reorder, "reorder_qty": reorder * 3,
            "price5": int(round(price * 0.9))}, owner_id)
        n += 1
        if packs:
            rows = []
            for code, factor, pprice in packs:
                rows.append({"unit": code, "factor": factor, "price": pprice, "barcode": _upc(n)})
                n += 1
            save_units(st, iid, rows, buy_unit=packs[-1][0])
        items.append((iid, department, cost, weight, packs))
        return iid

    for spirit in LIQUOR:
        for q, mult in zip(QUALITY, (1.0, 1.6, 2.6)):
            for size in rnd.sample(SIZES["Liquor"][:3], 2):
                base = {"750ML": 1499, "1.75L": 2999, "1L": 1899}[size] * mult
                price = int(round(base / 100)) * 100 - 1
                add(f"{q} {spirit}", "Liquor", size, price, int(price * rnd.uniform(0.62, 0.74)), "SAMPLE-SPIRITS",
                    "btl", weight=3.0 if q == "House" else 1.5, reorder=4)
    for w in WINES:
        for q, mult in zip(("House", "Estate"), (1.0, 2.2)):
            price = int(round(899 * mult / 100)) * 100 - 1
            add(f"{q} {w}", "Wine", "750ML", price, int(price * rnd.uniform(0.6, 0.7)), "SAMPLE-WINE", "btl",
                weight=1.0, reorder=6)
    for b in BEERS:
        single = rnd.choice([149, 179, 199, 229])
        packs = [("6pk", 6, single * 6 - 150), ("12pk", 12, single * 12 - 450), ("case", 24, single * 24 - 1100)]
        add(f"{b} 12OZ can", "Beer", "12OZ", single, int(single * 0.62), "SAMPLE-BEER", "can", weight=6.0,
            packs=packs, reorder=48)
    for department, names in OTHER:
        vendor = {"Mixers & soft drinks": "SAMPLE-MIX", "Snacks": "SAMPLE-SNACK", "Drink accessories": "SAMPLE-PARTY",
                  "Party supplies": "SAMPLE-PARTY", "Ice": "SAMPLE-MIX", "Tobacco": "SAMPLE-SNACK"}[department]
        for nm in names:
            price = rnd.choice([199, 299, 349, 399, 499, 699, 899, 1299])
            add(nm, department, "", price, int(price * rnd.uniform(0.45, 0.6)), vendor,
                weight=4.0 if department == "Ice" else 1.0, reorder=6)
    # employees
    staff = []
    for uname, full, role, pin, rate in (("manager", "Sam Manager", "manager", "2468", 1800),
                                         ("maria", "Maria Cashier", "cashier", "1111", 1300),
                                         ("dave", "Dave Cashier", "cashier", "2222", 1250),
                                         ("lee", "Lee Stock", "stock", "3333", 1400)):
        staff.append(s.auth.create_user(uname, full, role, pin=pin, pay_rate=rate, title=role.title()))
    customers = [s.lists.save(get_entity("customers"), {"name": nm, "phone": ph, "credit_limit": lim,
                                                         "price_level": lvl}, owner_id)
                 for nm, ph, lim, lvl in (("Corner Bar & Grill", "219-555-0141", 150000, 2),
                                          ("Lakeside Banquet Hall", "219-555-0192", 300000, 2),
                                          ("Pat Regular", "219-555-0107", 0, 1))]
    start = date.fromisoformat(dbmod.today_str()) - timedelta(days=days)
    # opening stock received the day before the history starts
    with _at(datetime.combine(start - timedelta(days=1), datetime.min.time()) + timedelta(hours=10)):
        by_vendor: dict = {}
        total_weight = sum(w for _i, _d, _c, w, _p in items)
        lines_expected = days * sales_per_day * 1.2 * 1.7          # busy days, ~1.7 lines per receipt
        for iid, department, cost, weight, packs in items:
            it = st.one("SELECT vendor_id FROM items WHERE id = ?", (iid,))
            lines_here = lines_expected * weight / total_weight
            if packs:                                                # ~11 cans per beer line on average
                cases = int(lines_here * 11 * 1.3 / 24) + 4
                by_vendor.setdefault(it["vendor_id"], []).append(
                    {"item_id": iid, "qty": cases, "unit": "case", "unit_cost": cost * 24})
            else:
                qty = int(lines_here * 1.4 * 1.3) + 6
                by_vendor.setdefault(it["vendor_id"], []).append({"item_id": iid, "qty": qty, "unit_cost": cost})
        for vid, lines in by_vendor.items():
            s.purchasing.receive(vid, lines, owner_id, bill_no=f"S{vid:03d}-OPEN")
    weights = [w for _i, _d, _c, w, _p in items]
    receipts = 0
    cashiers = [owner_id] + staff[:3]
    for d in range(days):
        day = start + timedelta(days=d)
        sunday = day.weekday() == 6
        open_h, close_h = (12, 20) if sunday else (9, 23)
        n_sales = int(sales_per_day * (1.4 if day.weekday() in (4, 5) else 0.7 if sunday else 1.0)
                      * rnd.uniform(0.85, 1.15))
        for station in (1, 2):
            s.db.station_id = station
            cashier = cashiers[(d + station) % len(cashiers)]
            with _at(datetime.combine(day, datetime.min.time()) + timedelta(hours=open_h)):
                s.timeclock.clock_in(cashier)
                shift = s.shifts.open(cashier, 15000)
            cash = 0
            times = sorted(rnd.uniform(open_h, close_h - 0.2) for _ in range(n_sales // 2))
            for t in times:
                c = Cart()
                for _ in range(rnd.choice([1, 1, 1, 2, 2, 3])):
                    iid, department, cost, weight, packs = rnd.choices(items, weights)[0]
                    unit = None
                    if packs:
                        unit = rnd.choice([None, "6pk", "12pk", "case", "12pk"])
                    c.lines.append(s.sales.make_line(s.inventory.item(iid), 1 if unit else rnd.choice([1, 1, 2]),
                                                     1, unit))
                if rnd.random() < 0.03:
                    s.sales.apply_sale_discount(c, 10, "Employee 10%")
                total = s.sales.totals(c)["total"]
                pay = rnd.random()
                with _at(datetime.combine(day, datetime.min.time()) + timedelta(hours=t)):
                    if pay < 0.55:
                        sid = s.sales.finalize(c, [{"method": "CARD", "tendered": total,
                                                    "ref": f"{rnd.randint(0, 9999):04d}"}], cashier, shift)
                    elif pay < 0.985:
                        given = ((total + 499) // 500) * 500 if rnd.random() < 0.6 else total
                        sid = s.sales.finalize(c, [{"method": "CASH", "tendered": given}], cashier, shift)
                        cash += total
                    else:
                        c.customer_id = rnd.choice(customers[:2])
                        sid = s.sales.finalize(c, [{"method": "CHARGE", "tendered": total}], cashier, shift)
                    receipts += 1
                    if rnd.random() < 0.01:
                        line = st.one("SELECT id FROM sale_lines WHERE sale_id = ? LIMIT 1", (sid,))
                        cn = s.sales.credit_note(sid, [{"line_id": line["id"], "qty": 1}], "CASH", cashier, shift,
                                                 "Customer changed mind")
                        cash -= st.val("SELECT total FROM sales WHERE id = ?", (cn,))
            with _at(datetime.combine(day, datetime.min.time()) + timedelta(hours=close_h, minutes=10)):
                expected = s.shifts.summary(shift)["expected_cash"]
                s.shifts.close(cashier, expected + rnd.choice([0, 0, 0, 0, -100, 25, -5]))
                s.timeclock.clock_out(cashier)
        if d % 7 == 6:                                      # weekly beer delivery
            with _at(datetime.combine(day, datetime.min.time()) + timedelta(hours=8)):
                lines = []
                for iid, department, cost, weight, packs in items:
                    if packs:
                        lines.append({"item_id": iid, "qty": 4, "unit": "case", "unit_cost": cost * 24})
                s.purchasing.receive(vend["SAMPLE-BEER"], lines, owner_id, bill_no=f"BEER-{day:%m%d}")
    s.db.station_id = own_station               # this computer keeps its own station number
    return {"items": len(items), "receipts": receipts, "days": days}
