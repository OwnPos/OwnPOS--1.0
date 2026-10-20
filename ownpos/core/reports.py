"""Report Center, grouped like QuickBooks (see docs/RESEARCH.md). Each report returns columns + rows that the
Excel-style grid shows and the Excel / PDF exporters write. Money values are cents; quantities are floats.

Returns count as negative sales everywhere. Quantities are in each item's own unit (a case of 24 = 24)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from .db import Database, today_str
from .ledger import Ledger

# column types: text, money, qty, int, pct, date, datetime, hours
Col = tuple  # (key, label, type)


@dataclass
class ReportResult:
    title: str
    columns: list[Col]
    rows: list[dict]
    totals: bool = True
    notes: str = ""
    params: dict = field(default_factory=dict)


@dataclass
class ReportDef:
    key: str
    title: str
    category: str
    params: list[str]
    fn: Callable
    description: str = ""
    perm: str = "reports"


REGISTRY: dict[str, ReportDef] = {}

CATEGORY_ORDER = ["Sales", "Inventory", "Purchases", "Vendors & Payables", "Customers & Receivables",
                  "Employees & Time Clock", "Cash & Drawers", "Sales Tax", "Company & Financial",
                  "Accountant", "Lists"]


def report(key, title, category, params=("date_range",), description="", perm="reports"):
    def deco(fn):
        REGISTRY[key] = ReportDef(key, title, category, list(params), fn, description, perm)
        return fn
    return deco


SIGN = "CASE WHEN s.doc_type = 'CN' THEN -1 ELSE 1 END"
PSIGN = "CASE WHEN p.kind = 'vendor_return' THEN -1 ELSE 1 END"     # returns to vendors count negative

# per-unit amounts and running balances are never added up in totals rows
NO_TOTAL_KEYS = {"price", "price1", "price2", "price3", "price4", "price5", "msrp", "cost", "cost_avg", "unit_cost",
                 "last_cost", "avg_sale", "credit_limit", "balance", "rate", "target", "est_cost", "margin",
                 "reorder_point", "reorder_qty", "share", "on_hand_now"}


def _range(p):
    today = today_str()
    return p.get("date_from") or today, p.get("date_to") or today


def _margin(row, profit_key="profit", sales_key="net"):
    s = row.get(sales_key) or 0
    row["margin"] = (row.get(profit_key, 0) / s * 100) if s else 0.0


def run_report(db: Database, key: str, params: dict | None = None) -> ReportResult:
    rd = REGISTRY[key]
    params = dict(params or {})
    res = rd.fn(db, params)
    res.params = params
    return res


def _dept_filter(p, alias="i", args=None):
    if p.get("department_id"):
        args.append(p["department_id"])
        return f" AND {alias}.department_id = ?"
    return ""


# ================================================================ Sales
@report("sales_summary", "Sales Summary by Day", "Sales",
        description="Each day: receipts, net sales, discounts, sales tax, total collected, returns, cost and profit.")
def sales_summary(db, p):
    a, b = _range(p)
    rows = db.dicts(
        f"SELECT s.sale_date AS date, SUM(CASE WHEN s.doc_type='INV' THEN 1 ELSE 0 END) AS receipts,"
        f" SUM(CASE WHEN s.doc_type='INV' THEN s.subtotal ELSE 0 END) AS gross,"
        f" SUM(CASE WHEN s.doc_type='INV' THEN s.discount ELSE 0 END) AS discount,"
        f" SUM({SIGN} * (s.taxable + s.nontaxable)) AS net, SUM({SIGN} * s.tax) AS tax,"
        f" SUM({SIGN} * s.total) AS total, SUM(CASE WHEN s.doc_type='CN' THEN s.total ELSE 0 END) AS returns,"
        f" SUM({SIGN} * s.cost_total) AS cost FROM sales s WHERE s.sale_date BETWEEN ? AND ?"
        f" GROUP BY s.sale_date ORDER BY s.sale_date", (a, b))
    for r in rows:
        r["weekday"] = date.fromisoformat(r["date"]).strftime("%a")
        r["profit"] = r["net"] - r["cost"]
        r["avg_sale"] = r["total"] // r["receipts"] if r["receipts"] else 0
        _margin(r)
    cols = [("date", "Date", "date"), ("weekday", "Day", "text"), ("receipts", "Receipts", "int"),
            ("gross", "Gross sales", "money"), ("discount", "Discounts", "money"), ("returns", "Returns", "money"),
            ("net", "Net sales", "money"), ("tax", "Sales tax", "money"), ("total", "Total collected", "money"),
            ("avg_sale", "Average sale", "money"), ("cost", "Cost", "money"), ("profit", "Gross profit", "money"),
            ("margin", "Margin %", "pct")]
    return ReportResult("Sales Summary by Day", cols, rows,
                        notes="Net sales = after discounts and returns, before tax. Returns are subtracted on the "
                              "day they were made.")


@report("sales_by_item", "Sales by Item Summary", "Sales", ("date_range", "department"))
def sales_by_item(db, p):
    a, b = _range(p)
    args = [a, b]
    cond = _dept_filter(p, "l", args)
    rows = db.dicts(
        f"SELECT COALESCE(i.item_no,'') AS item_no, COALESCE(i.upc,'') AS upc, COALESCE(i.name, MAX(l.description)) AS item,"
        f" COALESCE(i.size,'') AS size, COALESCE(d.name,'') AS department, COALESCE(i.unit,'') AS unit,"
        f" SUM({SIGN} * l.qty * l.unit_factor) AS qty, SUM({SIGN} * l.net) AS net,"
        f" SUM(CASE WHEN s.doc_type='INV' THEN l.discount ELSE 0 END) AS discount,"
        f" SUM({SIGN} * l.cost_total) AS cost, MAX(i.on_hand) AS on_hand_now"
        f" FROM sale_lines l JOIN sales s ON s.id = l.sale_id LEFT JOIN items i ON i.id = l.item_id"
        f" LEFT JOIN departments d ON d.id = l.department_id WHERE s.sale_date BETWEEN ? AND ?{cond}"
        f" GROUP BY l.item_id, i.item_no, i.upc, i.name, i.size, d.name, i.unit ORDER BY net DESC", args)
    for r in rows:
        r["profit"] = r["net"] - r["cost"]
        _margin(r)
    cols = [("item_no", "Item #", "text"), ("upc", "UPC", "text"), ("item", "Item", "text"), ("size", "Size", "text"),
            ("department", "Department", "text"), ("qty", "Qty sold", "qty"), ("unit", "Unit", "text"),
            ("discount", "Discounts", "money"), ("net", "Net sales", "money"), ("cost", "Cost", "money"),
            ("profit", "Gross profit", "money"), ("margin", "Margin %", "pct"), ("on_hand_now", "On hand now", "qty")]
    return ReportResult("Sales by Item Summary", cols, rows)


@report("sales_by_item_detail", "Sales by Item Detail", "Sales", ("date_range", "item"))
def sales_by_item_detail(db, p):
    a, b = _range(p)
    args = [a, b]
    cond = ""
    if p.get("item_id"):
        cond, args = " AND l.item_id = ?", args + [p["item_id"]]
    rows = db.dicts(
        f"SELECT s.sale_date AS date, s.created_at, s.receipt_no,"
        f" CASE WHEN s.doc_type='CN' THEN 'Return' ELSE 'Sale' END AS type, l.description AS item,"
        f" {SIGN} * l.qty AS qty, l.unit, l.price, {SIGN} * l.discount AS discount, {SIGN} * l.net AS net,"
        f" {SIGN} * l.cost_total AS cost, u.full_name AS associate, COALESCE(s.customer_name,'') AS customer"
        f" FROM sale_lines l JOIN sales s ON s.id = l.sale_id JOIN users u ON u.id = s.user_id"
        f" WHERE s.sale_date BETWEEN ? AND ?{cond} ORDER BY s.id, l.line_no LIMIT 50000", args)
    cols = [("created_at", "Date and time", "datetime"), ("receipt_no", "Receipt #", "text"), ("type", "Type", "text"),
            ("item", "Item", "text"), ("qty", "Qty", "qty"), ("unit", "Unit", "text"), ("price", "Price", "money"),
            ("discount", "Discount", "money"), ("net", "Net", "money"), ("cost", "Cost", "money"),
            ("associate", "Associate", "text"), ("customer", "Customer", "text")]
    return ReportResult("Sales by Item Detail", cols, rows)


@report("sales_by_department", "Sales by Department", "Sales")
def sales_by_department(db, p):
    a, b = _range(p)
    rows = db.dicts(
        f"SELECT COALESCE(d.name,'(none)') AS department, SUM({SIGN} * l.qty * l.unit_factor) AS qty,"
        f" SUM({SIGN} * l.net) AS net, SUM({SIGN} * l.tax) AS tax, SUM({SIGN} * l.cost_total) AS cost,"
        f" SUM(CASE WHEN s.doc_type='INV' THEN l.discount ELSE 0 END) AS discount"
        f" FROM sale_lines l JOIN sales s ON s.id = l.sale_id LEFT JOIN departments d ON d.id = l.department_id"
        f" WHERE s.sale_date BETWEEN ? AND ? GROUP BY d.name ORDER BY net DESC", (a, b))
    grand = sum(r["net"] for r in rows) or 1
    for r in rows:
        r["profit"] = r["net"] - r["cost"]
        r["share"] = r["net"] / grand * 100
        _margin(r)
    cols = [("department", "Department", "text"), ("qty", "Qty", "qty"), ("discount", "Discounts", "money"),
            ("net", "Net sales", "money"), ("share", "Share %", "pct"), ("tax", "Sales tax", "money"),
            ("cost", "Cost", "money"), ("profit", "Gross profit", "money"), ("margin", "Margin %", "pct")]
    return ReportResult("Sales by Department", cols, rows)


@report("sales_by_associate", "Sales by Associate", "Sales")
def sales_by_associate(db, p):
    a, b = _range(p)
    rows = db.dicts(
        f"SELECT u.full_name AS associate, SUM(CASE WHEN s.doc_type='INV' THEN 1 ELSE 0 END) AS receipts,"
        f" SUM(CASE WHEN s.doc_type='CN' THEN 1 ELSE 0 END) AS returns_n,"
        f" SUM(CASE WHEN s.doc_type='CN' THEN s.total ELSE 0 END) AS returns,"
        f" SUM(CASE WHEN s.doc_type='INV' THEN s.discount ELSE 0 END) AS discount,"
        f" SUM({SIGN} * (s.taxable + s.nontaxable)) AS net, SUM({SIGN} * s.total) AS total"
        f" FROM sales s JOIN users u ON u.id = s.user_id WHERE s.sale_date BETWEEN ? AND ?"
        f" GROUP BY u.id, u.full_name ORDER BY total DESC", (a, b))
    for r in rows:
        r["avg_sale"] = r["total"] // r["receipts"] if r["receipts"] else 0
    cols = [("associate", "Associate", "text"), ("receipts", "Receipts", "int"), ("discount", "Discounts", "money"),
            ("returns_n", "Returns", "int"), ("returns", "Returned amount", "money"), ("net", "Net sales", "money"),
            ("total", "Total collected", "money"), ("avg_sale", "Average sale", "money")]
    return ReportResult("Sales by Associate", cols, rows)


@report("sales_by_station", "Sales by Station", "Sales")
def sales_by_station(db, p):
    a, b = _range(p)
    rows = db.dicts(
        f"SELECT COALESCE(st.name, 'Station ' || s.station_id, '(none)') AS station,"
        f" SUM(CASE WHEN s.doc_type='INV' THEN 1 ELSE 0 END) AS receipts,"
        f" SUM({SIGN} * (s.taxable + s.nontaxable)) AS net, SUM({SIGN} * s.tax) AS tax, SUM({SIGN} * s.total) AS total"
        f" FROM sales s LEFT JOIN stations st ON st.id = s.station_id WHERE s.sale_date BETWEEN ? AND ?"
        f" GROUP BY s.station_id, st.name ORDER BY s.station_id", (a, b))
    cols = [("station", "Station", "text"), ("receipts", "Receipts", "int"), ("net", "Net sales", "money"),
            ("tax", "Sales tax", "money"), ("total", "Total collected", "money")]
    return ReportResult("Sales by Station", cols, rows)


@report("sales_by_hour", "Sales by Hour", "Sales")
def sales_by_hour(db, p):
    a, b = _range(p)
    rows = db.dicts(
        f"SELECT substr(s.created_at, 12, 2) AS hour, SUM(CASE WHEN s.doc_type='INV' THEN 1 ELSE 0 END) AS receipts,"
        f" SUM({SIGN} * s.total) AS total FROM sales s WHERE s.sale_date BETWEEN ? AND ?"
        f" GROUP BY substr(s.created_at, 12, 2) ORDER BY 1", (a, b))
    for r in rows:
        h = int(r["hour"])
        r["hour"] = f"{(h % 12) or 12}:00 {'AM' if h < 12 else 'PM'} - {(h % 12) or 12}:59 {'AM' if h < 12 else 'PM'}"
        r["avg_sale"] = r["total"] // r["receipts"] if r["receipts"] else 0
    cols = [("hour", "Hour", "text"), ("receipts", "Receipts", "int"), ("total", "Total", "money"),
            ("avg_sale", "Average sale", "money")]
    return ReportResult("Sales by Hour", cols, rows)


@report("sales_by_weekday", "Sales by Day of Week", "Sales")
def sales_by_weekday(db, p):
    a, b = _range(p)
    rows = db.dicts(
        f"SELECT EXTRACT(ISODOW FROM CAST(s.sale_date AS DATE)) AS dow, COUNT(DISTINCT s.sale_date) AS days,"
        f" SUM(CASE WHEN s.doc_type='INV' THEN 1 ELSE 0 END) AS receipts, SUM({SIGN} * s.total) AS total"
        f" FROM sales s WHERE s.sale_date BETWEEN ? AND ? GROUP BY 1 ORDER BY 1", (a, b))
    names = {1: "Monday", 2: "Tuesday", 3: "Wednesday", 4: "Thursday", 5: "Friday", 6: "Saturday", 7: "Sunday"}
    for r in rows:
        r["weekday"] = names[int(r["dow"])]
        r["avg_day"] = r["total"] // r["days"] if r["days"] else 0
    cols = [("weekday", "Day", "text"), ("days", "Days open", "int"), ("receipts", "Receipts", "int"),
            ("total", "Total", "money"), ("avg_day", "Average per day", "money")]
    return ReportResult("Sales by Day of Week", cols, rows)


@report("tenders", "Payments by Method (Tender Summary)", "Sales")
def tenders(db, p):
    a, b = _range(p)
    rows = db.dicts(
        "SELECT COALESCE(m.name, t.method_code) AS method, SUM(CASE WHEN s.doc_type='INV' THEN 1 ELSE 0 END) AS count,"
        " SUM(CASE WHEN s.doc_type='INV' THEN t.amount ELSE 0 END) AS received,"
        " SUM(CASE WHEN s.doc_type='CN' THEN t.amount ELSE 0 END) AS refunded,"
        f" SUM({SIGN} * t.amount) AS net FROM sale_tenders t JOIN sales s ON s.id = t.sale_id"
        " LEFT JOIN payment_methods m ON m.code = t.method_code WHERE s.sale_date BETWEEN ? AND ?"
        " GROUP BY t.method_code, m.name ORDER BY net DESC", (a, b))
    cols = [("method", "Payment method", "text"), ("count", "Receipts", "int"), ("received", "Received", "money"),
            ("refunded", "Refunded", "money"), ("net", "Net", "money")]
    return ReportResult("Payments by Method", cols, rows,
                        notes="Payments on house charge accounts are in Customers & Receivables, not here.")


@report("receipt_journal", "Receipt Journal", "Sales", ("date_range",))
def receipt_journal(db, p):
    a, b = _range(p)
    rows = db.dicts(
        "WITH pay AS (SELECT t.sale_id, string_agg(COALESCE(m.name, t.method_code), ' + ' ORDER BY t.id) AS paid_by"
        "   FROM sale_tenders t JOIN sales x ON x.id = t.sale_id LEFT JOIN payment_methods m ON m.code = t.method_code"
        "   WHERE x.sale_date BETWEEN ? AND ? GROUP BY t.sale_id)"
        " SELECT s.id, s.created_at, s.receipt_no, CASE s.doc_type WHEN 'CN' THEN 'Return' ELSE 'Sale' END AS type,"
        " COALESCE(o.receipt_no, '') AS original, COALESCE(s.customer_name, '') AS customer, u.full_name AS associate,"
        " COALESCE(st.name, '') AS station, "
        f" {SIGN} * (s.taxable + s.nontaxable) AS net, {SIGN} * s.tax AS tax, {SIGN} * s.total AS total,"
        " s.discount, COALESCE(s.discount_reason, '') AS discount_reason, COALESCE(pay.paid_by, '') AS paid_by,"
        " CASE WHEN s.is_active = 1 THEN '' ELSE 'Voided' END AS status, s.print_count AS prints"
        " FROM sales s JOIN users u ON u.id = s.user_id LEFT JOIN sales o ON o.id = s.ref_sale_id"
        " LEFT JOIN stations st ON st.id = s.station_id LEFT JOIN pay ON pay.sale_id = s.id"
        " WHERE s.sale_date BETWEEN ? AND ? ORDER BY s.id LIMIT 50000", (a, b, a, b))
    cols = [("created_at", "Date and time", "datetime"), ("receipt_no", "Receipt #", "text"), ("type", "Type", "text"),
            ("original", "Original #", "text"), ("customer", "Customer", "text"), ("associate", "Associate", "text"),
            ("station", "Station", "text"), ("discount", "Discount", "money"), ("discount_reason", "Discount reason", "text"),
            ("net", "Net", "money"), ("tax", "Tax", "money"), ("total", "Total", "money"), ("paid_by", "Paid by", "text"),
            ("status", "Status", "text"), ("prints", "Prints", "int")]
    return ReportResult("Receipt Journal", cols, rows)


@report("returns", "Returns and Voids", "Sales")
def returns_report(db, p):
    a, b = _range(p)
    rows = db.dicts(
        "SELECT s.created_at, s.receipt_no AS return_no, o.receipt_no AS original, o.created_at AS original_at,"
        " s.reason, u.full_name AS associate, s.total,"
        " CASE WHEN s.notes = 'Void' THEN 'Void' ELSE 'Return' END AS type,"
        " (SELECT string_agg(COALESCE(m.name, t.method_code), ' + ') FROM sale_tenders t"
        "   LEFT JOIN payment_methods m ON m.code = t.method_code WHERE t.sale_id = s.id) AS refunded_as"
        " FROM sales s JOIN users u ON u.id = s.user_id LEFT JOIN sales o ON o.id = s.ref_sale_id"
        " WHERE s.doc_type = 'CN' AND s.sale_date BETWEEN ? AND ? ORDER BY s.id", (a, b))
    cols = [("created_at", "Date and time", "datetime"), ("return_no", "Return #", "text"), ("type", "Type", "text"),
            ("original", "Original receipt", "text"), ("original_at", "Original date", "datetime"),
            ("reason", "Reason", "text"), ("associate", "By", "text"), ("refunded_as", "Refunded as", "text"),
            ("total", "Amount", "money")]
    return ReportResult("Returns and Voids", cols, rows)


@report("discounts", "Discounts Given", "Sales")
def discounts(db, p):
    a, b = _range(p)
    rows = db.dicts(
        "SELECT s.created_at, s.receipt_no, COALESCE(s.discount_reason, 'Line discount / price override') AS reason,"
        " u.full_name AS associate, s.subtotal AS gross, s.discount, s.total"
        " FROM sales s JOIN users u ON u.id = s.user_id"
        " WHERE s.doc_type = 'INV' AND s.discount > 0 AND s.sale_date BETWEEN ? AND ? ORDER BY s.id", (a, b))
    cols = [("created_at", "Date and time", "datetime"), ("receipt_no", "Receipt #", "text"), ("reason", "Discount", "text"),
            ("associate", "Associate", "text"), ("gross", "Before discount", "money"), ("discount", "Discount", "money"),
            ("total", "Receipt total", "money")]
    return ReportResult("Discounts Given", cols, rows)


@report("price_overrides", "Price Overrides", "Sales")
def price_overrides(db, p):
    a, b = _range(p)
    rows = db.dicts(
        "SELECT s.created_at, s.receipt_no, l.description AS item, l.qty, l.list_price, l.price,"
        " (l.price - l.list_price) AS difference, u.full_name AS associate FROM sale_lines l"
        " JOIN sales s ON s.id = l.sale_id JOIN users u ON u.id = s.user_id"
        " WHERE s.doc_type = 'INV' AND l.price <> l.list_price AND s.sale_date BETWEEN ? AND ? ORDER BY s.id", (a, b))
    cols = [("created_at", "Date and time", "datetime"), ("receipt_no", "Receipt #", "text"), ("item", "Item", "text"),
            ("qty", "Qty", "qty"), ("list_price", "Price was", "money"), ("price", "Sold at", "money"),
            ("difference", "Difference each", "money"), ("associate", "Associate", "text")]
    return ReportResult("Price Overrides", cols, rows, totals=False)


@report("sales_by_customer", "Sales by Customer Summary", "Customers & Receivables")
def sales_by_customer(db, p):
    a, b = _range(p)
    rows = db.dicts(
        f"SELECT COALESCE(c.name, 'Walk-in customers') AS customer, COALESCE(c.phone,'') AS phone,"
        f" SUM(CASE WHEN s.doc_type='INV' THEN 1 ELSE 0 END) AS receipts,"
        f" SUM({SIGN} * (s.taxable + s.nontaxable)) AS net, SUM({SIGN} * s.total) AS total,"
        f" MAX(s.sale_date) AS last_sale FROM sales s LEFT JOIN customers c ON c.id = s.customer_id"
        f" WHERE s.sale_date BETWEEN ? AND ? GROUP BY c.id, c.name, c.phone ORDER BY total DESC", (a, b))
    cols = [("customer", "Customer", "text"), ("phone", "Phone", "text"), ("receipts", "Receipts", "int"),
            ("net", "Net sales", "money"), ("total", "Total", "money"), ("last_sale", "Last purchase", "date")]
    return ReportResult("Sales by Customer Summary", cols, rows)


# ================================================================ Inventory
@report("inventory_valuation", "Inventory Valuation Summary", "Inventory", ("department",))
def inventory_valuation(db, p):
    args = []
    cond = _dept_filter(p, "i", args)
    rows = db.dicts(
        "SELECT i.item_no, COALESCE(i.upc,'') AS upc, i.name AS item, COALESCE(i.size,'') AS size,"
        " COALESCE(d.name,'') AS department, i.unit, i.on_hand, i.cost_avg AS cost,"
        " CAST(ROUND(CAST(i.on_hand * i.cost_avg AS NUMERIC)) AS BIGINT) AS value, i.price1 AS price,"
        " CAST(ROUND(CAST(i.on_hand * i.price1 AS NUMERIC)) AS BIGINT) AS retail_value"
        " FROM items i LEFT JOIN departments d ON d.id = i.department_id"
        f" WHERE i.active = 1 AND i.kind = 'inventory'{cond} ORDER BY d.sort, d.name, i.name", args)
    total = sum(r["value"] for r in rows) or 1
    for r in rows:
        r["share"] = r["value"] / total * 100
    cols = [("item_no", "Item #", "text"), ("upc", "UPC", "text"), ("item", "Item", "text"), ("size", "Size", "text"),
            ("department", "Department", "text"), ("on_hand", "On hand", "qty"), ("unit", "Unit", "text"),
            ("cost", "Avg cost", "money"), ("value", "Asset value", "money"), ("share", "% of value", "pct"),
            ("price", "Regular price", "money"), ("retail_value", "Retail value", "money")]
    return ReportResult("Inventory Valuation Summary", cols, rows,
                        notes="Asset value = on hand x average cost. Items with negative on-hand reduce the total.")


@report("inventory_by_department", "Inventory Value by Department", "Inventory", ())
def inventory_by_department(db, p):
    rows = db.dicts(
        "SELECT COALESCE(d.name,'(none)') AS department, COUNT(*) AS items, SUM(i.on_hand) AS qty,"
        " CAST(ROUND(CAST(SUM(i.on_hand * i.cost_avg) AS NUMERIC)) AS BIGINT) AS value,"
        " CAST(ROUND(CAST(SUM(i.on_hand * i.price1) AS NUMERIC)) AS BIGINT) AS retail_value"
        " FROM items i LEFT JOIN departments d ON d.id = i.department_id WHERE i.active = 1 AND i.kind = 'inventory'"
        " GROUP BY d.name, d.sort ORDER BY d.sort, d.name")
    cols = [("department", "Department", "text"), ("items", "Items", "int"), ("qty", "Units on hand", "qty"),
            ("value", "Asset value", "money"), ("retail_value", "Retail value", "money")]
    return ReportResult("Inventory Value by Department", cols, rows)


@report("physical_worksheet", "Physical Inventory Worksheet", "Inventory", ("department",))
def physical_worksheet(db, p):
    args = []
    cond = _dept_filter(p, "i", args)
    rows = db.dicts(
        "SELECT i.item_no, COALESCE(i.upc,'') AS upc, i.name AS item, COALESCE(i.size,'') AS size,"
        " COALESCE(d.name,'') AS department, i.unit, i.on_hand, '' AS counted"
        " FROM items i LEFT JOIN departments d ON d.id = i.department_id"
        f" WHERE i.active = 1 AND i.kind = 'inventory'{cond} ORDER BY d.sort, d.name, i.name", args)
    cols = [("item_no", "Item #", "text"), ("upc", "UPC", "text"), ("item", "Item", "text"), ("size", "Size", "text"),
            ("department", "Department", "text"), ("unit", "Unit", "text"), ("on_hand", "On hand", "qty"),
            ("counted", "Counted", "text")]
    return ReportResult("Physical Inventory Worksheet", cols, rows, totals=False,
                        notes="Print or export, count, then enter the counts in Inventory > Physical count.")


@report("reorder", "Items to Reorder", "Inventory", ())
def reorder(db, p):
    rows = db.dicts(
        "SELECT i.item_no, i.name AS item, COALESCE(i.size,'') AS size, i.unit, i.on_hand, i.reorder_point,"
        " i.reorder_qty, COALESCE(v.name,'') AS vendor, i.last_cost,"
        " (SELECT COALESCE(SUM(pl.qty * pl.unit_factor),0) FROM po_lines pl JOIN purchase_orders po ON po.id = pl.po_id"
        "   WHERE pl.item_id = i.id AND po.status IN ('draft','ordered')) AS on_order"
        " FROM items i LEFT JOIN vendors v ON v.id = i.vendor_id"
        " WHERE i.active = 1 AND i.kind = 'inventory' AND i.reorder_point > 0 AND i.on_hand <= i.reorder_point"
        " ORDER BY v.name, i.name")
    for r in rows:
        r["suggested"] = max((r["reorder_qty"] or r["reorder_point"] * 2) - r["on_hand"] - r["on_order"], 0)
    cols = [("item_no", "Item #", "text"), ("item", "Item", "text"), ("size", "Size", "text"), ("unit", "Unit", "text"),
            ("on_hand", "On hand", "qty"), ("reorder_point", "Reorder point", "qty"), ("on_order", "On order", "qty"),
            ("suggested", "Suggested order", "qty"), ("vendor", "Vendor", "text"), ("last_cost", "Last cost", "money")]
    return ReportResult("Items to Reorder", cols, rows, totals=False,
                        notes="Suggested = (Order up to, or 2 x reorder point) - on hand - already on order.")


@report("item_movement", "Inventory Valuation Detail (Item Movement)", "Inventory", ("date_range", "item"))
def item_movement(db, p):
    a, b = _range(p)
    args = [a, b + " 23:59:59"]
    cond = ""
    if p.get("item_id"):
        cond, args = " AND m.item_id = ?", args + [p["item_id"]]
    rows = db.dicts(
        "SELECT m.ts, i.name AS item, m.kind, m.doc_type, m.doc_id, m.qty, m.unit_cost, m.value,"
        " COALESCE(u.full_name,'') AS by_user, COALESCE(m.note,'') AS note FROM stock_moves m"
        " JOIN items i ON i.id = m.item_id LEFT JOIN users u ON u.id = m.user_id"
        f" WHERE m.ts BETWEEN ? AND ?{cond} ORDER BY m.id LIMIT 50000", args)
    labels = {"sale": "Sold", "return": "Customer return", "receive": "Received", "adjust": "Adjustment",
              "count": "Physical count", "opening": "Opening stock", "waste": "Breakage / waste", "revalue": "Cost correction"}
    for r in rows:
        r["kind"] = labels.get(r["kind"], r["kind"])
    cols = [("ts", "Date and time", "datetime"), ("item", "Item", "text"), ("kind", "Movement", "text"),
            ("doc_type", "Document", "text"), ("doc_id", "Doc id", "int"), ("qty", "Qty", "qty"),
            ("unit_cost", "Unit cost", "money"), ("value", "Value", "money"), ("by_user", "By", "text"),
            ("note", "Note", "text")]
    return ReportResult("Inventory Valuation Detail", cols, rows)


@report("best_sellers", "Best Sellers", "Inventory", ("date_range", "department"))
def best_sellers(db, p):
    res = sales_by_item(db, p)
    res.rows = sorted(res.rows, key=lambda r: -(r["qty"] or 0))[:200]
    res.title = "Best Sellers (top 200 by quantity)"
    return res


@report("slow_movers", "Slow Movers", "Inventory", ("days",))
def slow_movers(db, p):
    days = int(p.get("days") or db.get_setting("slow_mover_days", 60))
    since = (date.fromisoformat(today_str()) - timedelta(days=days)).isoformat()
    rows = db.dicts(
        "SELECT i.item_no, i.name AS item, COALESCE(i.size,'') AS size, i.on_hand, i.unit,"
        " CAST(ROUND(CAST(i.on_hand * i.cost_avg AS NUMERIC)) AS BIGINT) AS value,"
        " (SELECT MAX(s.sale_date) FROM sale_lines l JOIN sales s ON s.id = l.sale_id WHERE l.item_id = i.id) AS last_sold"
        " FROM items i WHERE i.active = 1 AND i.kind = 'inventory' AND i.on_hand > 0"
        " AND NOT EXISTS (SELECT 1 FROM sale_lines l JOIN sales s ON s.id = l.sale_id WHERE l.item_id = i.id"
        " AND s.sale_date >= ?) ORDER BY value DESC", (since,))
    cols = [("item_no", "Item #", "text"), ("item", "Item", "text"), ("size", "Size", "text"), ("on_hand", "On hand", "qty"),
            ("unit", "Unit", "text"), ("value", "Value at cost", "money"), ("last_sold", "Last sold", "date")]
    return ReportResult(f"Items not sold in {days} days", cols, rows)


@report("waste", "Breakage and Waste", "Inventory")
def waste(db, p):
    a, b = _range(p)
    rows = db.dicts(
        "SELECT substr(m.ts, 1, 10) AS date, ad.doc_no, i.name AS item, -m.qty AS qty, i.unit,"
        " COALESCE(ad.reason,'') AS reason, -m.value AS value, COALESCE(u.full_name,'') AS by_user"
        " FROM stock_moves m JOIN adjustments ad ON ad.id = m.doc_id AND m.doc_type = 'WS'"
        " JOIN items i ON i.id = m.item_id LEFT JOIN users u ON u.id = m.user_id"
        " WHERE substr(m.ts, 1, 10) BETWEEN ? AND ? ORDER BY m.id", (a, b))
    cols = [("date", "Date", "date"), ("doc_no", "Doc #", "text"), ("item", "Item", "text"), ("qty", "Qty", "qty"),
            ("unit", "Unit", "text"), ("reason", "Reason", "text"), ("value", "Value at cost", "money"),
            ("by_user", "By", "text")]
    return ReportResult("Breakage and Waste", cols, rows)


@report("adjustments", "Inventory Adjustments and Counts", "Inventory")
def adjustments(db, p):
    a, b = _range(p)
    rows = db.dicts(
        "SELECT ad.created_at, ad.doc_no, CASE ad.kind WHEN 'adjust' THEN 'Adjustment' WHEN 'count' THEN 'Physical count'"
        " WHEN 'opening' THEN 'Opening stock' ELSE 'Breakage / waste' END AS kind, COALESCE(ad.reason,'') AS reason,"
        " ad.value, COALESCE(u.full_name,'') AS by_user FROM adjustments ad LEFT JOIN users u ON u.id = ad.user_id"
        " WHERE substr(ad.created_at, 1, 10) BETWEEN ? AND ? ORDER BY ad.id", (a, b))
    cols = [("created_at", "Date and time", "datetime"), ("doc_no", "Doc #", "text"), ("kind", "Type", "text"),
            ("reason", "Reason", "text"), ("value", "Value change", "money"), ("by_user", "By", "text")]
    return ReportResult("Inventory Adjustments and Counts", cols, rows)


@report("item_margins", "Item Prices, Costs and Margins", "Inventory", ("department",), perm="costs")
def item_margins(db, p):
    args = []
    cond = _dept_filter(p, "i", args)
    rows = db.dicts(
        "SELECT i.item_no, i.name AS item, COALESCE(i.size,'') AS size, COALESCE(d.name,'') AS department,"
        " i.price1 AS price, i.cost_avg AS cost, i.last_cost, COALESCE(d.margin_target,0) AS target"
        " FROM items i LEFT JOIN departments d ON d.id = i.department_id"
        f" WHERE i.active = 1 AND i.kind = 'inventory'{cond} ORDER BY d.sort, i.name", args)
    for r in rows:
        r["margin"] = (r["price"] - r["cost"]) / r["price"] * 100 if r["price"] else 0
        r["flag"] = ("No price" if not r["price"] else "Below cost" if r["cost"] > r["price"]
                     else ("Under target" if r["target"] and r["margin"] < r["target"] else ""))
    cols = [("item_no", "Item #", "text"), ("item", "Item", "text"), ("size", "Size", "text"),
            ("department", "Department", "text"), ("price", "Regular price", "money"), ("cost", "Avg cost", "money"),
            ("last_cost", "Last cost", "money"), ("margin", "Margin %", "pct"), ("target", "Target %", "pct"),
            ("flag", "Check", "text")]
    return ReportResult("Item Prices, Costs and Margins", cols, rows, totals=False,
                        notes="Margin = (price - average cost) / price.")


@report("price_changes", "Price Change History", "Inventory")
def price_changes(db, p):
    a, b = _range(p)
    rows = db.dicts(
        "SELECT pc.changed_at, i.item_no, i.name, pc.field, pc.old_price, pc.new_price,"
        " pc.new_price - pc.old_price AS change, COALESCE(pc.reason,'') AS reason, COALESCE(pc.batch_no,'') AS batch_no,"
        " COALESCE(u.full_name,'') AS by_user FROM price_changes pc JOIN items i ON i.id = pc.item_id"
        " LEFT JOIN users u ON u.id = pc.user_id WHERE substr(pc.changed_at, 1, 10) BETWEEN ? AND ? ORDER BY pc.id DESC",
        (a, b))
    names = {"price1": "Regular", "price2": "Level 2", "price3": "Level 3", "price4": "Level 4", "price5": "Level 5"}
    for r in rows:
        r["field"] = names.get(r["field"], r["field"])
    cols = [("changed_at", "When", "datetime"), ("item_no", "Item #", "text"), ("name", "Item", "text"),
            ("field", "Price", "text"), ("old_price", "Was", "money"), ("new_price", "Now", "money"),
            ("change", "Change", "money"), ("reason", "Reason", "text"), ("batch_no", "Change #", "text"),
            ("by_user", "By", "text")]
    return ReportResult("Price Change History", cols, rows, totals=False)


# ================================================================ Purchases
@report("receiving_history", "Purchases by Vendor Detail (Receiving)", "Purchases")
def receiving_history(db, p):
    a, b = _range(p)
    rows = db.dicts(
        "SELECT p.bill_date AS date, p.doc_no, p.vendor_name AS vendor, COALESCE(p.bill_no,'') AS bill_no,"
        f" {PSIGN} * p.total AS total, CASE WHEN p.kind = 'vendor_return' THEN 'Returned to vendor'"
        " WHEN p.payment_account IS NOT NULL THEN 'Paid' ELSE 'On account' END AS status,"
        " COALESCE(p.due_date,'') AS due_date, COALESCE(u.full_name,'') AS received_by"
        " FROM purchases p LEFT JOIN users u ON u.id = p.user_id WHERE p.bill_date BETWEEN ? AND ? ORDER BY p.id", (a, b))
    cols = [("date", "Invoice date", "date"), ("doc_no", "Voucher #", "text"), ("vendor", "Vendor", "text"),
            ("bill_no", "Vendor invoice #", "text"), ("total", "Amount", "money"), ("status", "Status", "text"),
            ("due_date", "Due", "date"), ("received_by", "Received by", "text")]
    return ReportResult("Purchases by Vendor Detail", cols, rows)


@report("purchases_by_vendor", "Purchases by Vendor Summary", "Purchases")
def purchases_by_vendor(db, p):
    a, b = _range(p)
    rows = db.dicts(
        "SELECT COALESCE(v.name, p.vendor_name, '(none)') AS vendor, COUNT(*) AS vouchers,"
        f" SUM({PSIGN} * p.total) AS total,"
        " MAX(p.bill_date) AS last_date FROM purchases p LEFT JOIN vendors v ON v.id = p.vendor_id"
        " WHERE p.bill_date BETWEEN ? AND ? GROUP BY COALESCE(v.name, p.vendor_name, '(none)') ORDER BY total DESC",
        (a, b))
    cols = [("vendor", "Vendor", "text"), ("vouchers", "Invoices and returns", "int"), ("total", "Net total", "money"),
            ("last_date", "Last invoice", "date")]
    return ReportResult("Purchases by Vendor Summary", cols, rows)


@report("purchases_by_item", "Purchases by Item", "Purchases", ("date_range", "department"))
def purchases_by_item(db, p):
    a, b = _range(p)
    args = [a, b]
    cond = _dept_filter(p, "i", args)
    rows = db.dicts(
        f"SELECT i.item_no, i.name AS item, COALESCE(i.size,'') AS size, i.unit, SUM({PSIGN} * pl.qty) AS qty,"
        f" SUM({PSIGN} * pl.line_net) AS total,"
        " MIN(CASE WHEN p.kind = 'receive' THEN pl.unit_cost END) AS min_cost,"
        " MAX(CASE WHEN p.kind = 'receive' THEN pl.unit_cost END) AS max_cost"
        " FROM purchase_lines pl JOIN purchases p ON p.id = pl.purchase_id JOIN items i ON i.id = pl.item_id"
        f" WHERE p.bill_date BETWEEN ? AND ?{cond} GROUP BY i.id, i.item_no, i.name, i.size, i.unit ORDER BY total DESC",
        args)
    for r in rows:
        r["avg_cost"] = r["total"] / r["qty"] if r["qty"] else 0
    cols = [("item_no", "Item #", "text"), ("item", "Item", "text"), ("size", "Size", "text"), ("qty", "Net qty received", "qty"),
            ("unit", "Unit", "text"), ("total", "Total cost", "money"), ("avg_cost", "Average cost", "money"),
            ("min_cost", "Lowest cost", "money"), ("max_cost", "Highest cost", "money")]
    return ReportResult("Purchases by Item", cols, rows)


@report("open_pos", "Open Purchase Orders", "Purchases", ())
def open_pos(db, p):
    rows = db.dicts(
        "SELECT po.po_no, po.created_at, COALESCE(v.name,'') AS vendor, po.status,"
        " CASE WHEN po.created_by_agent = 1 THEN 'Reorder agent' ELSE COALESCE(u.full_name,'') END AS made_by,"
        " (SELECT COUNT(*) FROM po_lines WHERE po_id = po.id) AS lines,"
        " (SELECT CAST(ROUND(CAST(COALESCE(SUM(qty * est_cost * COALESCE(unit_factor, 1)),0) AS NUMERIC)) AS BIGINT)"
        " FROM po_lines"
        "   WHERE po_id = po.id) AS estimate"
        " FROM purchase_orders po LEFT JOIN vendors v ON v.id = po.vendor_id LEFT JOIN users u ON u.id = po.user_id"
        " WHERE po.status IN ('draft','ordered') ORDER BY po.id")
    cols = [("po_no", "PO #", "text"), ("created_at", "Created", "datetime"), ("vendor", "Vendor", "text"),
            ("status", "Status", "text"), ("made_by", "Made by", "text"), ("lines", "Lines", "int"),
            ("estimate", "Estimated cost", "money")]
    return ReportResult("Open Purchase Orders", cols, rows)


# ================================================================ Vendors & Payables
def _aging(rows_dc: list[dict], as_of: date, terms: dict) -> dict:
    """FIFO aging for one party: rows of {'date', 'debit', 'credit'} in the account's own direction
    (positive amount = what is owed). Returns buckets."""
    open_items = []      # [date, amount]
    credit_pool = 0
    for r in rows_dc:
        amt = r["amount"]
        if amt > 0:
            open_items.append([r["date"], amt])
        else:
            credit_pool += -amt
    for it in open_items:
        use = min(credit_pool, it[1])
        it[1] -= use
        credit_pool -= use
    b = {"current": 0, "d1_30": 0, "d31_60": 0, "d61_90": 0, "d90": 0}
    for d, amt in open_items:
        if amt <= 0:
            continue
        due = date.fromisoformat(d) + timedelta(days=terms.get("days", 0))
        late = (as_of - due).days
        key = "current" if late <= 0 else "d1_30" if late <= 30 else "d31_60" if late <= 60 else "d61_90" if late <= 90 else "d90"
        b[key] += amt
    b["unapplied_credit"] = credit_pool
    return b


def _party_aging(db, party_type: str, account: str, as_of: str, owed_sign: int):
    rows = db.all(
        "SELECT l.party_id, j.entry_date, l.debit, l.credit FROM journal_lines l JOIN journal j ON j.id = l.journal_id"
        " WHERE l.party_type = ? AND l.account = ? AND j.entry_date <= ? ORDER BY l.party_id, j.entry_date, j.id",
        (party_type, account, as_of))
    by: dict[int, list] = {}
    for r in rows:
        by.setdefault(r["party_id"], []).append({"date": r["entry_date"], "amount": owed_sign * (r["debit"] - r["credit"])})
    return by


@report("ap_aging", "A/P Aging Summary", "Vendors & Payables", ("date_to",), perm="vendor_payments")
def ap_aging(db, p):
    as_of = p.get("date_to") or today_str()
    by = _party_aging(db, "vendor", "2000", as_of, -1)
    out = []
    for vid, items in by.items():
        v = db.one("SELECT name, terms_days FROM vendors WHERE id = ?", (vid,))
        bk = _aging(items, date.fromisoformat(as_of), {"days": int(v["terms_days"] or 0) if v else 0})
        total = bk["current"] + bk["d1_30"] + bk["d31_60"] + bk["d61_90"] + bk["d90"] - bk["unapplied_credit"]
        if total or bk["unapplied_credit"]:
            out.append({"vendor": v["name"] if v else f"#{vid}", **bk, "total": total})
    out.sort(key=lambda r: -r["total"])
    cols = [("vendor", "Vendor", "text"), ("current", "Current", "money"), ("d1_30", "1 - 30", "money"),
            ("d31_60", "31 - 60", "money"), ("d61_90", "61 - 90", "money"), ("d90", "> 90", "money"),
            ("unapplied_credit", "Credits / prepaid", "money"), ("total", "Total owed", "money")]
    return ReportResult(f"A/P Aging Summary as of {as_of}", cols, out,
                        notes="Days late are counted from each invoice's due date (invoice date + vendor terms). "
                              "Payments are applied to the oldest invoices first.")


@report("vendor_balances", "Vendor Balance Summary", "Vendors & Payables", (), perm="vendor_payments")
def vendor_balances(db, p):
    rows = db.dicts(
        "SELECT v.code, v.name, COALESCE(v.phone,'') AS phone, COALESCE(SUM(l.credit - l.debit),0) AS owed,"
        " (SELECT MAX(bill_date) FROM purchases pu WHERE pu.vendor_id = v.id) AS last_purchase"
        " FROM vendors v LEFT JOIN journal_lines l ON l.party_type = 'vendor' AND l.party_id = v.id AND l.account = '2000'"
        " GROUP BY v.id, v.code, v.name, v.phone HAVING COALESCE(SUM(l.credit - l.debit),0) <> 0 ORDER BY 4 DESC")
    cols = [("code", "Code", "text"), ("name", "Vendor", "text"), ("phone", "Phone", "text"),
            ("owed", "We owe", "money"), ("last_purchase", "Last invoice", "date")]
    return ReportResult("Vendor Balance Summary", cols, rows)


@report("unpaid_bills", "Unpaid Bills Detail", "Vendors & Payables", (), perm="vendor_payments")
def unpaid_bills(db, p):
    from .purchasing import PurchasingService
    from .inventory import Inventory
    bills = PurchasingService(db, Ledger(db), Inventory(db)).open_bills()
    today = date.fromisoformat(today_str())
    rows = []
    for bl in bills:
        due = bl["due_date"] or bl["bill_date"]
        rows.append({"vendor": bl["vendor_name"], "bill_date": bl["bill_date"], "bill_no": bl["bill_no"] or "",
                     "doc_no": bl["doc_no"], "due": due, "days_late": max((today - date.fromisoformat(due)).days, 0),
                     "total": bl["total"], "open": bl["open"]})
    cols = [("vendor", "Vendor", "text"), ("bill_date", "Invoice date", "date"), ("bill_no", "Vendor invoice #", "text"),
            ("doc_no", "Voucher #", "text"), ("due", "Due", "date"), ("days_late", "Days late", "int"),
            ("total", "Invoice amount", "money"), ("open", "Still open", "money")]
    return ReportResult("Unpaid Bills Detail", cols, rows,
                        notes="Vendor payments are applied to that vendor's oldest invoices first.")


@report("vendor_payments", "Vendor Payments", "Vendors & Payables", perm="vendor_payments")
def vendor_payments(db, p):
    a, b = _range(p)
    rows = db.dicts(
        "SELECT vp.pay_date, v.name AS vendor, vp.amount, COALESCE(a.name, vp.account) AS paid_from,"
        " COALESCE(vp.ref,'') AS ref, COALESCE(u.full_name,'') AS by_user FROM vendor_payments vp"
        " JOIN vendors v ON v.id = vp.vendor_id LEFT JOIN accounts a ON a.code = vp.account"
        " LEFT JOIN users u ON u.id = vp.user_id WHERE vp.pay_date BETWEEN ? AND ? ORDER BY vp.id", (a, b))
    cols = [("pay_date", "Date", "date"), ("vendor", "Vendor", "text"), ("amount", "Amount", "money"),
            ("paid_from", "Paid from", "text"), ("ref", "Check #", "text"), ("by_user", "By", "text")]
    return ReportResult("Vendor Payments", cols, rows)


# ================================================================ Customers & Receivables
@report("ar_aging", "A/R Aging Summary (House Charges)", "Customers & Receivables", ("date_to",))
def ar_aging(db, p):
    as_of = p.get("date_to") or today_str()
    by = _party_aging(db, "customer", "1200", as_of, 1)
    out = []
    for cid, items in by.items():
        c = db.one("SELECT name, phone, terms_days, credit_limit FROM customers WHERE id = ?", (cid,))
        bk = _aging(items, date.fromisoformat(as_of), {"days": int(c["terms_days"] or 0) if c else 0})
        total = bk["current"] + bk["d1_30"] + bk["d31_60"] + bk["d61_90"] + bk["d90"] - bk["unapplied_credit"]
        if total or bk["unapplied_credit"]:
            out.append({"customer": c["name"] if c else f"#{cid}", "phone": (c["phone"] or "") if c else "", **bk,
                        "total": total, "credit_limit": c["credit_limit"] if c else 0})
    out.sort(key=lambda r: -r["total"])
    cols = [("customer", "Customer", "text"), ("phone", "Phone", "text"), ("current", "Current", "money"),
            ("d1_30", "1 - 30", "money"), ("d31_60", "31 - 60", "money"), ("d61_90", "61 - 90", "money"),
            ("d90", "> 90", "money"), ("unapplied_credit", "Unapplied payments", "money"), ("total", "Total owed", "money"),
            ("credit_limit", "Charge limit", "money")]
    return ReportResult(f"A/R Aging Summary as of {as_of}", cols, out,
                        notes="Days late are counted from each charge's due date (date + customer terms). "
                              "Payments are applied to the oldest charges first.")


@report("customer_balances", "Customer Balance Summary", "Customers & Receivables", ())
def customer_balances(db, p):
    rows = db.dicts(
        "SELECT c.code, c.name, COALESCE(c.phone,'') AS phone, c.credit_limit,"
        " COALESCE(SUM(CASE WHEN l.account='1200' THEN l.debit - l.credit END),0) AS owes,"
        " COALESCE(SUM(CASE WHEN l.account='2210' THEN l.credit - l.debit END),0) AS store_credit,"
        " (SELECT MAX(sale_date) FROM sales s WHERE s.customer_id = c.id) AS last_sale"
        " FROM customers c LEFT JOIN journal_lines l ON l.party_type = 'customer' AND l.party_id = c.id"
        " GROUP BY c.id, c.code, c.name, c.phone, c.credit_limit"
        " HAVING COALESCE(SUM(CASE WHEN l.account='1200' THEN l.debit - l.credit END),0) <> 0"
        "     OR COALESCE(SUM(CASE WHEN l.account='2210' THEN l.credit - l.debit END),0) <> 0 ORDER BY 5 DESC")
    cols = [("code", "Customer #", "text"), ("name", "Customer", "text"), ("phone", "Phone", "text"),
            ("owes", "Owes (house charge)", "money"), ("credit_limit", "Charge limit", "money"),
            ("store_credit", "Store credit", "money"), ("last_sale", "Last purchase", "date")]
    return ReportResult("Customer Balance Summary", cols, rows)


@report("customer_payments", "Payments on Account", "Customers & Receivables")
def customer_payments(db, p):
    a, b = _range(p)
    rows = db.dicts(
        "SELECT cp.created_at, c.name AS customer, cp.amount, COALESCE(m.name, cp.method_code) AS method,"
        " COALESCE(cp.ref,'') AS ref, COALESCE(u.full_name,'') AS by_user FROM customer_payments cp"
        " JOIN customers c ON c.id = cp.customer_id LEFT JOIN payment_methods m ON m.code = cp.method_code"
        " LEFT JOIN users u ON u.id = cp.user_id WHERE substr(cp.created_at, 1, 10) BETWEEN ? AND ? ORDER BY cp.id", (a, b))
    cols = [("created_at", "Date and time", "datetime"), ("customer", "Customer", "text"), ("amount", "Amount", "money"),
            ("method", "Method", "text"), ("ref", "Reference", "text"), ("by_user", "By", "text")]
    return ReportResult("Payments on Account", cols, rows)


# ================================================================ Employees & Time Clock
@report("time_detail", "Time Clock Detail", "Employees & Time Clock", ("date_range", "user"))
def time_detail(db, p):
    a, b = _range(p)
    from .timeclock import TimeClock
    rows = TimeClock(db).entries(a, b, p.get("user_id"))
    for r in rows:
        r["status"] = ("Still clocked in" if not r["clock_out"] else
                       ("Fixed: " + (r["edit_reason"] or "")) if r["edited_by"] else "")
    cols = [("full_name", "Employee", "text"), ("clock_in", "Clock in", "datetime"), ("clock_out", "Clock out", "datetime"),
            ("hours", "Hours", "hours"), ("status", "Note", "text")]
    return ReportResult("Time Clock Detail", cols, rows,
                        notes="A punch belongs to the day it was clocked in.")


@report("payroll_hours", "Payroll Hours by Week", "Employees & Time Clock", ("date_range",))
def payroll_hours(db, p):
    a, b = _range(p)
    from .timeclock import TimeClock
    rows = TimeClock(db).payroll(a, b, with_pay=False)
    cols = [("employee", "Employee", "text"), ("week_start", "Week from", "date"), ("week_end", "to", "date"),
            ("hours", "Hours", "hours"), ("regular", "Regular", "hours"), ("overtime", "Overtime", "hours"),
            ("punches", "Punches", "int"), ("open_punches", "Still open", "int"), ("edited", "Fixed punches", "int")]
    return ReportResult("Payroll Hours by Week", cols, rows,
                        notes="Whole workweeks around the dates chosen. Overtime = hours over 40 in a workweek "
                              "(hourly employees marked for overtime).")


@report("payroll_estimate", "Payroll Estimate (Gross Pay)", "Employees & Time Clock", ("date_range",), perm="payroll")
def payroll_estimate(db, p):
    a, b = _range(p)
    from .timeclock import TimeClock
    rows = TimeClock(db).payroll(a, b, with_pay=True)
    for r in rows:
        r["pay_type"] = {"hourly": "Hourly", "salary": "Salary / week", "none": "-"}.get(r.get("pay_type"), "")
    cols = [("employee", "Employee", "text"), ("week_start", "Week from", "date"), ("week_end", "to", "date"),
            ("regular", "Regular hours", "hours"), ("overtime", "Overtime hours", "hours"), ("pay_type", "Pay type", "text"),
            ("rate", "Rate", "money"), ("gross_pay", "Estimated gross pay", "money")]
    return ReportResult("Payroll Estimate", cols, rows,
                        notes="An estimate for the accountant: regular hours x rate + overtime hours x rate x 1.5. "
                              "Taxes and withholdings are NOT included.")


# ================================================================ Cash & Drawers
@report("z_outs", "Drawer Shifts and Z-Outs", "Cash & Drawers", ("date_range",))
def z_outs(db, p):
    a, b = _range(p)
    rows = db.dicts(
        "SELECT s.z_number, COALESCE(st.name, 'Station ' || s.station_id) AS station, s.opened_at, s.closed_at,"
        " u.full_name AS cashier, s.opening_float, s.expected_cash, s.counted_cash, s.over_short, s.status"
        " FROM shifts s JOIN users u ON u.id = s.user_id LEFT JOIN stations st ON st.id = s.station_id"
        " WHERE substr(s.opened_at, 1, 10) BETWEEN ? AND ? ORDER BY s.id", (a, b))
    cols = [("z_number", "Z #", "text"), ("station", "Station", "text"), ("opened_at", "Opened", "datetime"),
            ("closed_at", "Closed", "datetime"), ("cashier", "Cashier", "text"), ("opening_float", "Opening cash", "money"),
            ("expected_cash", "Expected cash", "money"), ("counted_cash", "Counted cash", "money"),
            ("over_short", "Over / short", "money"), ("status", "Status", "text")]
    return ReportResult("Drawer Shifts and Z-Outs", cols, rows)


@report("cash_moves", "Payouts, Drops and No-Sales", "Cash & Drawers")
def cash_moves(db, p):
    a, b = _range(p)
    rows = db.dicts(
        "SELECT m.created_at, COALESCE(st.name, 'Station ' || m.station_id, '') AS station,"
        " CASE m.kind WHEN 'payout' THEN 'Payout' WHEN 'drop' THEN 'Drop' WHEN 'paid_in' THEN 'Paid in'"
        " ELSE 'No sale' END AS kind, m.amount, COALESCE(m.reason,'') AS reason, COALESCE(a.name,'') AS account,"
        " COALESCE(m.note,'') AS note, COALESCE(u.full_name,'') AS by_user FROM cash_moves m"
        " LEFT JOIN accounts a ON a.code = m.account LEFT JOIN users u ON u.id = m.user_id"
        " LEFT JOIN stations st ON st.id = m.station_id"
        " WHERE substr(m.created_at, 1, 10) BETWEEN ? AND ? ORDER BY m.id", (a, b))
    cols = [("created_at", "Date and time", "datetime"), ("station", "Station", "text"), ("kind", "Type", "text"),
            ("amount", "Amount", "money"), ("reason", "Reason", "text"), ("account", "Account", "text"),
            ("note", "Note", "text"), ("by_user", "By", "text")]
    return ReportResult("Payouts, Drops and No-Sales", cols, rows)


@report("day_closings", "Day Closing Summary", "Cash & Drawers")
def day_closings(db, p):
    a, b = _range(p)
    from .shifts import ShiftService
    ss = ShiftService(db, Ledger(db))
    rows = []
    days = [r["sale_date"] for r in db.all("SELECT DISTINCT sale_date FROM sales WHERE sale_date BETWEEN ? AND ?"
                                           " ORDER BY 1", (a, b))]
    for d in days:
        dc = ss.day_closing(d)
        cash = next((t["net"] for t in dc["tenders"] if t["code"] == "CASH"), 0)
        card = next((t["net"] for t in dc["tenders"] if t["code"] == "CARD"), 0)
        rows.append({"date": d, "receipts": dc["n_inv"], "net": dc["net"], "tax": dc["tax"],
                     "total": dc["sales_total"] - dc["returns_total"], "cash": cash, "card": card,
                     "other": dc["sales_total"] - dc["returns_total"] - cash - card, "over_short": dc["over_short"],
                     "open_drawers": len(dc["open_drawers"])})
    cols = [("date", "Date", "date"), ("receipts", "Receipts", "int"), ("net", "Net sales", "money"),
            ("tax", "Sales tax", "money"), ("total", "Total collected", "money"), ("cash", "Cash", "money"),
            ("card", "Card", "money"), ("other", "Other", "money"), ("over_short", "Over / short", "money"),
            ("open_drawers", "Drawers still open", "int")]
    return ReportResult("Day Closing Summary", cols, rows)


# ================================================================ Sales Tax
def _quarter_bounds(d: date) -> tuple[date, date]:
    q0 = (d.month - 1) // 3 * 3 + 1
    start = date(d.year, q0, 1)
    end = date(d.year + (q0 + 3 > 12), (q0 + 3 - 1) % 12 + 1, 1) - timedelta(days=1)
    return start, end


@report("sales_tax_liability", "Sales Tax Liability (ST-103)", "Sales Tax", ("date_range",), perm="financial_reports")
def sales_tax_liability(db, p):
    a, b = _range(p)
    rows = db.dicts(
        f"SELECT substr(s.sale_date, 1, 7) AS month,"
        f" SUM({SIGN} * (s.taxable + s.nontaxable)) AS total_sales, SUM({SIGN} * s.nontaxable) AS exempt,"
        f" SUM({SIGN} * s.taxable) AS taxable, SUM({SIGN} * s.tax) AS collected"
        f" FROM sales s WHERE s.sale_date BETWEEN ? AND ? GROUP BY 1 ORDER BY 1", (a, b))
    rate = float(db.val("SELECT rate FROM tax_codes WHERE code = 'IN7'", default=7.0))
    from .tax import tax_on
    for r in rows:
        r["at_rate"] = tax_on(r["taxable"], rate)
        r["difference"] = r["collected"] - r["at_rate"]
    if rows:
        tot = {k: sum(r[k] for r in rows) for k in ("total_sales", "exempt", "taxable", "collected")}
        tot["at_rate"] = tax_on(tot["taxable"], rate)
        tot["difference"] = tot["collected"] - tot["at_rate"]
        rows.append({"month": "Period total", **tot, "style": "total"})
    cols = [("month", "Month", "text"), ("total_sales", "Line 1 Total sales", "money"),
            ("exempt", "Line 2 Exempt / not taxable", "money"), ("taxable", "Line 4 Taxable sales", "money"),
            ("at_rate", f"Line 5 Tax at {rate:g}%", "money"), ("collected", "Tax charged on receipts", "money"),
            ("difference", "Rounding difference", "money")]
    payable = Ledger(db).balance("2100", b)
    return ReportResult(f"Sales Tax Liability {a} to {b}", cols, rows, totals=False,
                        notes=(f"Total sales are net of discounts and returns, before tax. Line 5 is Line 4 x {rate:g}% "
                               "as the form computes it; 'Tax charged on receipts' is what customers paid (each receipt "
                               "rounded on its own). Line 6 collection allowance, interest and penalties are left to "
                               "the accountant. Sales tax payable (account 2100) on " + b + ": $" + f"{payable / 100:,.2f}."))


@report("sales_tax_daily", "Sales Tax by Day", "Sales Tax", perm="financial_reports")
def sales_tax_daily(db, p):
    a, b = _range(p)
    rows = db.dicts(
        f"SELECT s.sale_date AS date, SUM({SIGN} * s.taxable) AS taxable, SUM({SIGN} * s.nontaxable) AS nontaxable,"
        f" SUM({SIGN} * s.tax) AS tax FROM sales s WHERE s.sale_date BETWEEN ? AND ? GROUP BY s.sale_date ORDER BY 1",
        (a, b))
    cols = [("date", "Date", "date"), ("taxable", "Taxable sales", "money"), ("nontaxable", "Not taxable", "money"),
            ("tax", "Sales tax", "money")]
    return ReportResult("Sales Tax by Day", cols, rows)


# ================================================================ Company & Financial
def _statement_rows(sections):
    rows = []
    for title, items, total_label, total in sections:
        if title is None:
            rows.append({"line": total_label, "amount": total, "style": "grand"})
            continue
        rows.append({"line": title, "amount": None, "style": "header"})
        for r in items:
            rows.append({"line": f"   {r['code']}  {r['name']}", "amount": r["amount"]})
        rows.append({"line": total_label, "amount": total, "style": "total"})
    return rows


@report("profit_loss", "Profit and Loss Standard", "Company & Financial", perm="financial_reports")
def profit_loss(db, p):
    a, b = _range(p)
    pl = Ledger(db).profit_and_loss(a, b)
    rows = _statement_rows([
        ("Income", pl["income"] + [{**r, "amount": -r["amount"]} for r in pl["contra"]], "Total income",
         pl["total_income"]),
        ("Cost of goods sold", pl["cogs"], "Total cost of goods sold", pl["total_cogs"]),
    ])
    rows.append({"line": "Gross profit", "amount": pl["gross_profit"], "style": "total"})
    rows += _statement_rows([("Expenses", pl["expense"], "Total expenses", pl["total_expense"]),
                             (None, [], "Net income", pl["net_profit"])])
    return ReportResult(f"Profit and Loss {a} to {b}", [("line", "Account", "text"), ("amount", "Amount", "money")],
                        rows, totals=False)


@report("profit_loss_monthly", "Profit and Loss by Month", "Company & Financial", perm="financial_reports")
def profit_loss_monthly(db, p):
    a, b = _range(p)
    months = []
    d = date.fromisoformat(a).replace(day=1)
    end = date.fromisoformat(b)
    while d <= end and len(months) < 24:
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        months.append((max(d, date.fromisoformat(a)).isoformat(), min(nxt - timedelta(days=1), end).isoformat(),
                       d.strftime("%b %Y")))
        d = nxt
    led = Ledger(db)
    rows = [{"line": "Total income"}, {"line": "Total cost of goods sold"}, {"line": "Gross profit"},
            {"line": "Total expenses"}, {"line": "Net income", "style": "grand"}]
    cols = [("line", "", "text")]
    for i, (ma, mb, label) in enumerate(months):
        pl = led.profit_and_loss(ma, mb)
        key = f"m{i}"
        cols.append((key, label, "money"))
        for r, v in zip(rows, (pl["total_income"], pl["total_cogs"], pl["gross_profit"], pl["total_expense"],
                               pl["net_profit"])):
            r[key] = v
    return ReportResult("Profit and Loss by Month", cols, rows, totals=False)


@report("balance_sheet", "Balance Sheet Standard", "Company & Financial", ("date_to",), perm="financial_reports")
def balance_sheet(db, p):
    b = p.get("date_to") or today_str()
    bs = Ledger(db).balance_sheet(b)
    rows = _statement_rows([
        ("Assets", bs["assets"], "Total assets", bs["total_assets"]),
        ("Liabilities", bs["liabilities"], "Total liabilities", bs["total_liabilities"]),
        ("Equity", bs["equity"] + [{"code": "", "name": "Net income to date (not yet closed)",
                                     "amount": bs["current_earnings"]}], "Total equity", bs["total_equity"]),
        (None, [], "Total liabilities and equity", bs["total_liabilities"] + bs["total_equity"]),
    ])
    return ReportResult(f"Balance Sheet as of {b}", [("line", "Account", "text"), ("amount", "Amount", "money")],
                        rows, totals=False, notes="" if bs["balanced"] else "Warning: the balance sheet does not balance.")


@report("expenses", "Expenses by Account", "Company & Financial", perm="financial_reports")
def expenses_report(db, p):
    a, b = _range(p)
    rows = db.dicts(
        "SELECT e.expense_date AS date, e.doc_no, a.code || ' ' || a.name AS account, COALESCE(e.payee, v.name, '') AS payee,"
        " e.amount, COALESCE(pa.name, e.paid_from) AS paid_from, COALESCE(e.ref,'') AS ref, COALESCE(e.note,'') AS note"
        " FROM expenses e JOIN accounts a ON a.code = e.account LEFT JOIN vendors v ON v.id = e.vendor_id"
        " LEFT JOIN accounts pa ON pa.code = e.paid_from WHERE e.expense_date BETWEEN ? AND ? ORDER BY e.expense_date, e.id",
        (a, b))
    cols = [("date", "Date", "date"), ("doc_no", "Doc #", "text"), ("account", "Account", "text"), ("payee", "Payee", "text"),
            ("amount", "Amount", "money"), ("paid_from", "Paid from", "text"), ("ref", "Ref / check #", "text"),
            ("note", "Note", "text")]
    return ReportResult("Expenses by Account", cols, rows)


# ================================================================ Accountant
@report("trial_balance", "Trial Balance", "Accountant", ("date_to",), perm="financial_reports")
def trial_balance(db, p):
    b = p.get("date_to") or today_str()
    rows = Ledger(db).trial_balance(b)
    cols = [("code", "Account #", "text"), ("name", "Account", "text"), ("type", "Type", "text"),
            ("debit", "Debit", "money"), ("credit", "Credit", "money")]
    return ReportResult(f"Trial Balance as of {b}", cols, rows)


@report("general_ledger", "General Ledger", "Accountant", ("date_range", "account"), perm="financial_reports")
def general_ledger(db, p):
    a, b = _range(p)
    acct = p.get("account") or "1000"
    rows = Ledger(db).general_ledger(acct, a, b)
    name = db.val("SELECT name FROM accounts WHERE code = ?", (acct,), acct)
    cols = [("date", "Date", "date"), ("doc_type", "Type", "text"), ("doc_no", "Num", "text"), ("memo", "Memo", "text"),
            ("debit", "Debit", "money"), ("credit", "Credit", "money"), ("balance", "Balance", "money")]
    return ReportResult(f"General Ledger: {acct} {name}", cols, rows, totals=False)


@report("journal", "Journal", "Accountant", perm="financial_reports")
def journal(db, p):
    a, b = _range(p)
    rows = db.dicts(
        "SELECT j.entry_date AS date, j.doc_type, COALESCE(j.doc_no,'') AS doc_no, j.memo, l.account,"
        " a.name AS account_name, l.debit, l.credit FROM journal j JOIN journal_lines l ON l.journal_id = j.id"
        " JOIN accounts a ON a.code = l.account WHERE j.entry_date BETWEEN ? AND ? ORDER BY j.id, l.id LIMIT 100000", (a, b))
    cols = [("date", "Date", "date"), ("doc_type", "Type", "text"), ("doc_no", "Num", "text"), ("memo", "Memo", "text"),
            ("account", "Account #", "text"), ("account_name", "Account", "text"), ("debit", "Debit", "money"),
            ("credit", "Credit", "money")]
    return ReportResult("Journal", cols, rows)


@report("audit_trail", "Audit Trail", "Accountant", ("date_range", "user"), perm="financial_reports")
def audit_trail(db, p):
    a, b = _range(p)
    args = [a, b + " 23:59:59"]
    cond = ""
    if p.get("user_id"):
        cond, args = " AND user_id = ?", args + [p["user_id"]]
    rows = db.dicts(f"SELECT ts, COALESCE(username,'') AS user_name, COALESCE(CAST(station_id AS TEXT),'') AS station,"
                    f" action, COALESCE(entity,'') AS entity, COALESCE(entity_id,'') AS entity_id, details FROM audit_log"
                    f" WHERE ts BETWEEN ? AND ?{cond} ORDER BY id LIMIT 100000", args)
    cols = [("ts", "Date and time", "datetime"), ("user_name", "User", "text"), ("station", "Station", "text"),
            ("action", "Action", "text"), ("entity", "Record", "text"), ("entity_id", "Record id", "text"),
            ("details", "Details", "text")]
    return ReportResult("Audit Trail", cols, rows, totals=False)


@report("reprints", "Receipt Reprints", "Accountant")
def reprints(db, p):
    a, b = _range(p)
    rows = db.dicts("SELECT pl.ts, s.receipt_no, pl.copy_no, COALESCE(u.full_name,'') AS by_user FROM print_log pl"
                    " JOIN sales s ON s.id = pl.sale_id LEFT JOIN users u ON u.id = pl.user_id"
                    " WHERE pl.copy_no > 0 AND substr(pl.ts, 1, 10) BETWEEN ? AND ? ORDER BY pl.id", (a, b))
    cols = [("ts", "Printed at", "datetime"), ("receipt_no", "Receipt #", "text"), ("copy_no", "Copy #", "int"),
            ("by_user", "By", "text")]
    return ReportResult("Receipt Reprints", cols, rows, totals=False)


# ================================================================ Lists
@report("price_list", "Item Price List", "Lists", ("department",))
def price_list(db, p):
    args = []
    cond = _dept_filter(p, "i", args)
    rows = db.dicts(
        "SELECT i.item_no, COALESCE(i.upc,'') AS upc, i.name AS item, COALESCE(i.size,'') AS size,"
        " COALESCE(d.name,'') AS department, i.price1, i.price2, i.price3, i.price4, i.price5, i.on_hand"
        " FROM items i LEFT JOIN departments d ON d.id = i.department_id"
        f" WHERE i.active = 1{cond} ORDER BY d.sort, d.name, i.name", args)
    names = {r["code"]: r["name"] for r in db.all("SELECT code, name FROM lookups WHERE list_name = 'price_level'")}
    cols = [("item_no", "Item #", "text"), ("upc", "UPC", "text"), ("item", "Item", "text"), ("size", "Size", "text"),
            ("department", "Department", "text")] + \
           [(f"price{i}", names.get(str(i), f"Price {i}"), "money") for i in range(1, 6)] + [("on_hand", "On hand", "qty")]
    return ReportResult("Item Price List", cols, rows, totals=False,
                        notes="A price level of 0.00 sells at the Regular price.")


@report("customer_list", "Customer Phone List", "Lists", ())
def customer_list(db, p):
    rows = db.dicts("SELECT code, name, COALESCE(phone,'') AS phone, COALESCE(email,'') AS email, price_level,"
                    " credit_limit FROM customers WHERE active = 1 ORDER BY name")
    cols = [("code", "Customer #", "text"), ("name", "Name", "text"), ("phone", "Phone", "text"),
            ("email", "Email", "text"), ("price_level", "Price level", "int"), ("credit_limit", "Charge limit", "money")]
    return ReportResult("Customer Phone List", cols, rows, totals=False)


@report("vendor_list", "Vendor Phone List", "Lists", ())
def vendor_list(db, p):
    rows = db.dicts("SELECT code, name, COALESCE(contact,'') AS contact, COALESCE(phone,'') AS phone,"
                    " COALESCE(email,'') AS email, COALESCE(account_no,'') AS account_no, terms_days FROM vendors"
                    " WHERE active = 1 ORDER BY name")
    cols = [("code", "Code", "text"), ("name", "Vendor", "text"), ("contact", "Contact", "text"), ("phone", "Phone", "text"),
            ("email", "Email", "text"), ("account_no", "Our account #", "text"), ("terms_days", "Terms (days)", "int")]
    return ReportResult("Vendor Phone List", cols, rows, totals=False)


@report("account_list", "Account Listing", "Lists", (), perm="financial_reports")
def account_list(db, p):
    rows = db.dicts("SELECT code, name, type, COALESCE(subtype,'') AS subtype FROM accounts WHERE active = 1 ORDER BY code")
    led = Ledger(db)
    for r in rows:
        r["balance"] = led.balance(r["code"])
    cols = [("code", "Account #", "text"), ("name", "Account", "text"), ("type", "Type", "text"),
            ("subtype", "Money kind", "text"), ("balance", "Balance", "money")]
    return ReportResult("Account Listing", cols, rows, totals=False)


@report("employee_list", "Employee List", "Lists", (), perm="employees")
def employee_list(db, p):
    rows = db.dicts("SELECT u.username, u.full_name, COALESCE(u.title,'') AS title, r.name AS role,"
                    " COALESCE(u.phone,'') AS phone FROM users u LEFT JOIN roles r ON r.code = u.role"
                    " WHERE u.active = 1 ORDER BY u.full_name")
    cols = [("username", "Login", "text"), ("full_name", "Name", "text"), ("title", "Job title", "text"),
            ("role", "Role", "text"), ("phone", "Phone", "text")]
    return ReportResult("Employee List", cols, rows, totals=False)


def categories_of_reports() -> dict[str, list[ReportDef]]:
    out: dict[str, list[ReportDef]] = {c: [] for c in CATEGORY_ORDER}
    for rd in REGISTRY.values():
        out.setdefault(rd.category, []).append(rd)
    return {k: v for k, v in out.items() if v}
