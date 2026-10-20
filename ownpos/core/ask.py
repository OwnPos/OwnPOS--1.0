"""Ask OwnPOS: everyday questions answered WITHOUT AI (decision of 17 Oct 2026, option C).

A question such as "best sellers in beer last week" is read by plain rules into one of the built-in reports
plus its dates and filters. The report computes every figure exactly (integer cents in SQL), and the short
answer sentence is built from the report's own rows, so nothing here can invent or mis-add a number. A
question the rules do not understand returns None; the screen then offers the optional AI fallback.

Every answer says which report and which dates were used, so the person can check it in the Report Center."""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from .money import fmt_money
from .reports import NO_TOTAL_KEYS, REGISTRY, run_report

MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
MONTHS.pop("may", None)
MONTHS["may"] = 5
WEEKDAYS = {d.lower(): i for i, d in enumerate(calendar.day_name)}
WEEKDAYS.update({d.lower(): i for i, d in enumerate(calendar.day_abbr)})


@dataclass
class Parsed:
    report: str
    params: dict = field(default_factory=dict)
    period: str = ""                  # "last week (10/04/2026 - 10/10/2026)"
    notes: list = field(default_factory=list)


@dataclass
class Answer:
    text: str
    report: str
    params: dict
    result: object = None


# --------------------------------------------------------------------------------- dates
def _us(d: date) -> str:
    return d.strftime("%m/%d/%Y")


def _week_start(d: date, first: int) -> date:
    return d - timedelta(days=(d.weekday() - first) % 7)


def _quarter(year: int, q: int) -> tuple[date, date]:
    start = date(year, 3 * (q - 1) + 1, 1)
    end_month = start.month + 2
    return start, date(year, end_month, calendar.monthrange(year, end_month)[1])


def _parse_date(text: str, today: date) -> date | None:
    text = text.strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        y, mo, d = (int(x) for x in m.groups())
    else:
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", text)
        if not m:
            return None
        mo, d = int(m.group(1)), int(m.group(2))
        y = int(m.group(3)) if m.group(3) else today.year
        if y < 100:
            y += 2000
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_period(q: str, today: date, week_starts: int = 6) -> tuple[date, date, str] | None:
    """(first day, last day, words) for the date phrase in the question, or None when it has none."""
    t = today
    s = q.lower()
    # explicit range: "from 10/1 to 10/15", "10/1 - 10/15", "between 2026-10-01 and 2026-10-15"
    m = re.search(r"(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s*(?:to|-|–|and|through|thru)\s*"
                  r"(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)", s)
    if m:
        a, b = _parse_date(m.group(1), t), _parse_date(m.group(2), t)
        if a and b:
            a, b = min(a, b), max(a, b)
            return a, b, f"{_us(a)} to {_us(b)}"
    m = re.search(r"\b(?:last|past)\s+(\d{1,3})\s+days?\b", s)
    if m:
        n = max(1, int(m.group(1)))
        return t - timedelta(days=n - 1), t, f"the last {n} days"
    if re.search(r"\btoday\b|\btonight\b|\bso far today\b", s):
        return t, t, "today"
    if re.search(r"\byesterday\b", s):
        y = t - timedelta(days=1)
        return y, y, "yesterday"
    if re.search(r"\b(this|current)\s+week\b|\bweek to date\b", s):
        return _week_start(t, week_starts), t, "this week"
    if re.search(r"\b(last|previous|past)\s+week\b", s):
        a = _week_start(t, week_starts) - timedelta(days=7)
        return a, a + timedelta(days=6), "last week"
    if re.search(r"\b(this|current)\s+month\b|\bmonth to date\b", s):
        return t.replace(day=1), t, "this month"
    if re.search(r"\b(last|previous|past)\s+month\b", s):
        end = t.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end, "last month"
    m = re.search(r"\bq([1-4])(?:\s+(\d{4}))?\b|\b(first|second|third|fourth)\s+quarter(?:\s+(?:of\s+)?(\d{4}))?", s)
    if m:
        qn = int(m.group(1)) if m.group(1) else ["first", "second", "third", "fourth"].index(m.group(3)) + 1
        year = int(m.group(2) or m.group(4) or t.year)
        a, b = _quarter(year, qn)
        return a, min(b, t) if a <= t <= b else b, f"Q{qn} {year}"
    if re.search(r"\b(this|current)\s+quarter\b|\bquarter to date\b", s):
        qn = (t.month - 1) // 3 + 1
        return _quarter(t.year, qn)[0], t, "this quarter"
    if re.search(r"\b(last|previous|past)\s+quarter\b", s):
        qn = (t.month - 1) // 3 + 1
        year, qn = (t.year, qn - 1) if qn > 1 else (t.year - 1, 4)
        a, b = _quarter(year, qn)
        return a, b, f"last quarter (Q{qn} {year})"
    if re.search(r"\b(this|current)\s+year\b|\byear to date\b|\bytd\b", s):
        return date(t.year, 1, 1), t, "this year"
    if re.search(r"\b(last|previous|past)\s+year\b", s):
        return date(t.year - 1, 1, 1), date(t.year - 1, 12, 31), f"last year ({t.year - 1})"
    # a month name: "in october", "september 2026"
    for name, num in sorted(MONTHS.items(), key=lambda kv: -len(kv[0])):
        m = re.search(rf"\b{name}\b(?:\s+(\d{{4}}))?", s)
        if m and not (name == "may" and re.search(r"\bmay\s+(i|we|you)\b", s)):
            year = int(m.group(1)) if m.group(1) else (t.year if num <= t.month else t.year - 1)
            a = date(year, num, 1)
            b = date(year, num, calendar.monthrange(year, num)[1])
            return a, min(b, t) if a <= t <= b else b, f"{calendar.month_name[num]} {year}"
    # a weekday: "last saturday", "on friday" = the most recent one (today counts only if said "today")
    for name, num in sorted(WEEKDAYS.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{name}\b", s):
            back = (t.weekday() - num) % 7 or 7
            d = t - timedelta(days=back)
            return d, d, f"{calendar.day_name[num]} {_us(d)}"
    m = re.search(r"\bon\s+(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)", s) or \
        re.search(r"\b(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4})\b", s)
    if m:
        d = _parse_date(m.group(1), t)
        if d:
            return d, d, _us(d)
    m = re.search(r"\bsince\s+(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)", s)
    if m:
        d = _parse_date(m.group(1), t)
        if d:
            return d, t, f"since {_us(d)}"
    return None


# --------------------------------------------------------------------------------- topics
# (report key, needs a date range, words that point to it). The first rule with a matching word wins, so
# the specific ones come first.
RULES: list[tuple[str, list[str]]] = [
    ("sales_tax_liability", [r"st-?103", r"tax return", r"sales tax (?:liability|owed|due|for the quarter)"]),
    ("sales_tax_daily", [r"sales tax", r"\btax\b"]),
    ("profit_loss", [r"profit and loss", r"profit & loss", r"\bp ?& ?l\b", r"\bp ?and ?l\b", r"net income",
                     r"net profit", r"\bprofit\b(?! margin)", r"\bincome statement\b"]),
    ("balance_sheet", [r"balance sheet"]),
    ("trial_balance", [r"trial balance"]),
    ("item_margins", [r"\bmargins?\b", r"below cost", r"under cost"]),
    ("reorder", [r"low (?:on )?stock", r"\breorder", r"running (?:low|out)", r"out of stock", r"need to order",
                 r"what (?:should|do) we order"]),
    ("slow_movers", [r"slow", r"not sell", r"dead stock", r"isn'?t selling", r"not moving"]),
    ("best_sellers", [r"best ?sell", r"top (?:\d+ )?(?:items|sellers|products)", r"most (?:sold|popular)",
                      r"sell(?:s|ing)? the most", r"what sold"]),
    ("sales_by_hour", [r"by hour", r"busiest hours?", r"per hour", r"hourly"]),
    ("sales_by_weekday", [r"day of (?:the )?week", r"busiest day", r"by weekday"]),
    ("sales_by_department", [r"by department", r"per department", r"each department", r"departments"]),
    ("sales_by_associate", [r"by (?:cashier|employee|associate|clerk)", r"each (?:cashier|employee)",
                            r"cashiers? sales"]),
    ("sales_by_station", [r"by (?:station|register|lane)", r"each (?:station|register)"]),
    ("tenders", [r"cash (?:and|vs\.?|versus) card", r"by (?:payment|tender)", r"card sales", r"cash sales",
                 r"how (?:much|many) (?:in|by|on) (?:cash|card|check)", r"payment methods?"]),
    ("customer_balances", [r"who owes", r"owes? us", r"house (?:charge|account)", r"receivables?",
                           r"customer balances?"]),
    ("unpaid_bills", [r"unpaid bills", r"bills? due", r"due to vendors", r"overdue"]),
    ("vendor_balances", [r"we owe", r"payables?", r"vendor balances?", r"owe (?:the )?vendors?"]),
    ("inventory_by_department", [r"inventory (?:value|worth)", r"stock (?:value|worth)", r"value of (?:the )?"
                                 r"(?:inventory|stock)", r"worth of (?:the )?(?:inventory|stock)"]),
    ("returns", [r"\breturns?\b", r"\bvoids?\b", r"refunds?"]),
    ("discounts", [r"discounts?", r"employee (?:10|15|20)"]),
    ("price_overrides", [r"price overrides?", r"price changes? at the register"]),
    ("price_changes", [r"price changes?", r"price history"]),
    ("cash_moves", [r"payouts?", r"paid outs?", r"cash drops?", r"no ?sales?\b"]),
    ("z_outs", [r"z-?outs?", r"drawers?", r"over ?/? ?short", r"short (?:in|on) the drawer"]),
    ("day_closings", [r"day clos", r"end of day"]),
    ("payroll_estimate", [r"payroll", r"gross pay", r"wages", r"pay (?:for|of) (?:the )?employees"]),
    ("payroll_hours", [r"hours worked", r"overtime", r"time ?clock", r"how many hours", r"\bhours\b"]),
    ("purchases_by_item", [r"bought (?:of|how many)", r"purchases? (?:by|of) items?", r"received (?:of|how many)"]),
    ("purchases_by_vendor", [r"purchases?", r"bought from", r"received from", r"spent (?:with|on) vendors?"]),
    ("expenses", [r"expenses?", r"spend(?:ing)?\b"]),
    ("waste", [r"breakage", r"broken", r"waste", r"spoil"]),
    ("adjustments", [r"adjustments?", r"counts?\b", r"shrink"]),
    ("sales_by_customer", [r"by customer", r"best customers?", r"top customers?"]),
    ("sales_by_item", [r"by item", r"each item", r"per item", r"items? sold"]),
    ("sales_summary", [r"\bsales?\b", r"\bsold\b", r"revenue", r"how (?:much|many) did we (?:make|do|sell)",
                       r"how did we do", r"\btakings?\b", r"\bbusiness\b"]),
]

STOP_WORDS = {"the", "an", "of", "bottles", "bottle", "cases", "case", "cans", "can", "units", "packs", "pack",
              "did", "we", "you", "us", "they", "our", "it", "money", "tax", "sales", "sale", "sell", "sold",
              "make", "made", "much", "many", "do", "does", "in", "on", "at", "to", "and", "or", "for", "is",
              "are", "was", "were", "have", "has", "had", "total", "all", "any", "some", "this", "that", "last",
              "week", "month", "year", "today", "yesterday", "store"}

NO_DATE_REPORTS = {"reorder", "customer_balances", "vendor_balances", "unpaid_bills", "inventory_by_department",
                   "item_margins", "slow_movers"}


def _department(db, q: str) -> tuple[int | None, str]:
    s = q.lower()
    best = None
    for r in db.all("SELECT id, name FROM departments WHERE active = 1"):
        name = r["name"].lower()
        words = [w for w in re.split(r"[^a-z0-9]+", name) if len(w) > 2]
        if re.search(rf"\b{re.escape(name)}\b", s) or (words and re.search(rf"\b{re.escape(words[0])}s?\b", s)):
            if best is None or len(name) > len(best[1]):
                best = (r["id"], r["name"])
    return (best[0], best[1]) if best else (None, "")


def _item_phrase(q: str) -> str:
    """The item words of 'how many X did we sell' / 'sales of X', or ''."""
    s = q.lower()
    m = re.search(r"(?:how (?:much|many)|units of|cases of|bottles of)\s+(.+?)\s+(?:did|have|has|were|was|do)\b", s) or \
        re.search(r"\bsales of\s+(.+?)(?:\s+(?:in|on|this|last|today|yesterday|since|from|for)\b|[?.]|$)", s)
    if not m:
        return ""
    words = [w for w in re.split(r"[^a-z0-9.]+", m.group(1)) if len(w) >= 2 and w not in STOP_WORDS]
    return " ".join(words) if re.search(r"\b(sell|sold|sales?)\b", s) else ""


def _item(db, q: str) -> list[dict]:
    """Items named in 'how many X did we sell': all words after sell/of/sold must be in the item name."""
    s = q.lower()
    m = re.search(r"(?:how (?:much|many)|units of|cases of|bottles of)\s+(.+?)\s+(?:did|have|has|were|was|do)\b", s) or \
        re.search(r"\bsales of\s+(.+?)(?:\s+(?:in|on|this|last|today|yesterday|since|from|for)\b|[?.]|$)", s)
    if not m:
        return []
    words = [w for w in re.split(r"[^a-z0-9.]+", m.group(1)) if len(w) >= 2 and w not in STOP_WORDS]
    if not words:
        return []
    sql = "SELECT id, item_no, name, size FROM items WHERE active = 1" + "".join(
        " AND (lower(name) LIKE ? OR lower(COALESCE(size,'')) LIKE ?)" for _ in words) + " ORDER BY name LIMIT 11"
    args = []
    for w in words:
        args += [f"%{w}%", f"%{w}%"]
    return db.dicts(sql, args)


def _employee(db, q: str) -> tuple[int | None, str]:
    s = q.lower()
    for r in db.all("SELECT id, full_name, username FROM users"):
        first = (r["full_name"] or "").split()[0].lower() if r["full_name"] else ""
        if (first and len(first) > 2 and re.search(rf"\b{re.escape(first)}\b", s)) or \
                re.search(rf"\b{re.escape(r['username'].lower())}\b", s):
            return r["id"], r["full_name"]
    return None, ""


def parse(db, question: str, today: date, week_starts: int = 6) -> Parsed | None:
    q = (question or "").strip()
    if not q:
        return None
    s = q.lower()
    key = None
    for rk, pats in RULES:
        if any(re.search(p, s) for p in pats):
            key = rk
            break
    items = _item(db, q)
    phrase = _item_phrase(q)
    if key in (None, "sales_summary", "sales_by_item") and items:
        key = "sales_by_item_detail" if len(items) == 1 else "sales_by_item"
    elif key in (None, "sales_summary") and phrase:
        key = "sales_by_item"                # an item was asked about but no item matches its name
    if key is None:
        return None
    per = parse_period(q, today, week_starts)
    if key == "sales_tax_daily" and per and (per[1] - per[0]).days > 31:
        key = "sales_tax_liability"          # a month or more: the return's lines, month by month
    if key == "payroll_hours" and _employee(db, q)[0]:
        key = "time_detail"                  # one named employee: their punches
    rd = REGISTRY[key]
    params: dict = {}
    out = Parsed(key, params)
    if "date_range" in rd.params:
        a, b, words = per if per else (today, today, "today")
        if key == "sales_tax_liability" and not per:
            qn = (today.month - 1) // 3 + 1
            year, qn = (today.year, qn - 1) if qn > 1 else (today.year - 1, 4)
            a, b = _quarter(year, qn)
            words = f"last quarter (Q{qn} {year})"
        if a > today and b > today:
            out.notes.append("Those dates are in the future.")
        params["date_from"], params["date_to"] = a.isoformat(), b.isoformat()
        out.period = f"{words} ({_us(a)} - {_us(b)})" if a != b else f"{words} ({_us(a)})"
    elif "date_to" in rd.params:
        b = per[1] if per else today
        params["date_to"] = b.isoformat()
        out.period = f"as of {_us(b)}"
    elif per and key in NO_DATE_REPORTS:
        out.notes.append("This report shows how things stand now; the dates in the question were not used.")
    if "department" in rd.params:
        did, dname = _department(db, q)
        if did:
            params["department_id"] = did
            out.notes.append(f"Department: {dname}.")
    if "item" in rd.params and len(items) == 1:
        params["item_id"] = items[0]["id"]
        out.notes.append(f"Item: {items[0]['name']} {items[0]['size'] or ''}".strip() + ".")
    elif key == "sales_by_item" and phrase and not items:
        out.notes.append(f"No item's name contains '{phrase}', so all items are shown. Check the spelling, or "
                         "find the item in the Item List.")
    elif key == "sales_by_item" and len(items) > 1:
        names = ", ".join(f"{i['name']} {i['size'] or ''}".strip() for i in items[:10])
        out.notes.append(f"Several items match ({names}{' ...' if len(items) > 10 else ''}); all items are shown "
                         "- type more of the name to pick one.")
    if "user" in rd.params:
        uid, uname = _employee(db, q)
        if uid:
            params["user_id"] = uid
            out.notes.append(f"Employee: {uname}.")
    if "days" in rd.params:
        m = re.search(r"(\d{1,3})\s+days", s)
        if m:
            params["days"] = int(m.group(1))
    return out


# --------------------------------------------------------------------------------- answers
def _money(c) -> str:
    return f"${fmt_money(c)}"


def _sum(rows, key):
    return sum(r.get(key) or 0 for r in rows if not r.get("style") and isinstance(r.get(key), (int, float)))


def summarize(key: str, res) -> str:
    """One or two plain sentences built only from the report's own rows."""
    rows = [r for r in res.rows if not r.get("style")]
    if key in ("profit_loss", "balance_sheet", "profit_loss_monthly"):
        totals = [r for r in res.rows if r.get("style") in ("total", "grand") and r.get("amount") is not None]
        return "; ".join(f"{r['line'].strip()} {_money(r['amount'])}" for r in totals) or "No entries."
    if not rows:
        return "Nothing was recorded for that."
    if key == "sales_summary":
        return (f"Net sales {_money(_sum(rows, 'net'))} before tax, sales tax {_money(_sum(rows, 'tax'))}, "
                f"total collected {_money(_sum(rows, 'total'))} on {int(_sum(rows, 'receipts')):,} receipts "
                f"(returns {_money(_sum(rows, 'returns'))}, discounts {_money(_sum(rows, 'discount'))}).")
    if key in ("best_sellers", "sales_by_item"):
        top = sorted(rows, key=lambda r: -(r.get("qty") or 0))[:5]
        lst = "; ".join(f"{r['item']} {r.get('size') or ''}".strip() + f" ({r['qty']:g} {r.get('unit') or ''}, "
                        f"{_money(r['net'])})" for r in top)
        return f"Top by quantity: {lst}. {len(rows):,} item(s) sold, net {_money(_sum(rows, 'net'))}."
    if key == "sales_by_item_detail":
        sold = sum(r["qty"] for r in rows if r.get("type") != "Return")
        back = sum(r["qty"] for r in rows if r.get("type") == "Return")
        return (f"{sold:g} sold" + (f", {back:g} returned" if back else "") +
                f" on {len(rows):,} receipt line(s); net {_money(_sum(rows, 'net'))}.")
    if key == "sales_tax_liability":
        tot = next((r for r in res.rows if r.get("style") == "total"), None) or {}
        return (f"Taxable sales (Line 4) {_money(tot.get('taxable', 0))}; tax at the rate (Line 5) "
                f"{_money(tot.get('at_rate', 0))}; tax charged on receipts {_money(tot.get('collected', 0))}.")
    if key == "sales_tax_daily":
        return f"Sales tax {_money(_sum(rows, 'tax'))} on taxable sales of {_money(_sum(rows, 'taxable'))}."
    if key == "reorder":
        names = ", ".join(f"{r['item']} {r.get('size') or ''}".strip() for r in rows[:8])
        return f"{len(rows):,} item(s) at or below their reorder point: {names}{' ...' if len(rows) > 8 else ''}."
    if key == "slow_movers":
        return f"{len(rows):,} item(s) in stock have not sold; their value at cost is {_money(_sum(rows, 'value'))}."
    if key == "customer_balances":
        owing = [r for r in rows if (r.get("owes") or 0) > 0]
        lst = "; ".join(f"{r['name']} {_money(r['owes'])}" for r in sorted(owing, key=lambda r: -r["owes"])[:8])
        return (f"{len(owing)} customer(s) owe {_money(sum(r['owes'] for r in owing))}" +
                (f": {lst}." if lst else "."))
    if key == "vendor_balances":
        owed = [r for r in rows if (r.get("owed") or 0) > 0]
        return f"The store owes {len(owed)} vendor(s) {_money(sum(r['owed'] for r in owed))}."
    if key == "unpaid_bills":
        late = [r for r in rows if (r.get("days_late") or 0) > 0]
        return (f"{len(rows)} unpaid bill(s), {_money(_sum(rows, 'open'))} still open; {len(late)} past due "
                f"({_money(sum(r['open'] for r in late))}).")
    if key == "inventory_by_department":
        return (f"Stock on hand is worth {_money(_sum(rows, 'value'))} at cost and {_money(_sum(rows, 'retail_value'))}"
                f" at regular prices.")
    if key == "sales_by_hour":
        best = max(rows, key=lambda r: r.get("total") or 0)
        return f"Busiest hour: {best['hour']} with {_money(best['total'])} on {best['receipts']} receipts."
    if key == "sales_by_weekday":
        best = max(rows, key=lambda r: r.get("total") or 0)
        return f"Busiest day: {best['weekday']} with {_money(best['total'])} in total."
    # any other list: count and the main money columns
    money_cols = [(k, lbl) for k, lbl, t in res.columns if t == "money" and k not in NO_TOTAL_KEYS]
    parts = [f"{len(rows):,} row(s)"]
    if getattr(res, "totals", True):
        for k, lbl in money_cols[:3]:
            parts.append(f"{lbl.lower()} {_money(_sum(rows, k))}")
    return ", ".join(parts) + "."


def answer(db, question: str, user=None, today: date | None = None) -> Answer | None:
    """The answer, or None when the question is not understood. Respects the employee's report permissions."""
    from .db import today_str
    today = today or date.fromisoformat(today_str())
    ws = db.get_setting("week_starts", 6)
    p = parse(db, question, today, 6 if ws in (None, "") else int(ws))
    if p is None:
        return None
    rd = REGISTRY[p.report]
    if user is not None and not user.can(rd.perm):
        return Answer(f"That is answered by the report '{rd.title}', which your role may not see.", p.report, p.params)
    res = run_report(db, p.report, dict(p.params))
    text = summarize(p.report, res)
    head = f"{rd.title}" + (f", {p.period}" if p.period else "") + ": "
    tail = (" " + " ".join(p.notes)) if p.notes else ""
    return Answer(head + text + tail, p.report, p.params, res)


EXAMPLES = [
    "Sales today", "How did we do last week?", "Best sellers in beer this month", "What's low on stock?",
    "Sales tax last quarter", "Who owes us money?", "Busiest hours last Saturday", "Sales by cashier yesterday",
    "How many Tito's did we sell in September?", "Profit and loss this year", "Cash vs card today",
    "Unpaid bills", "Hours worked last week", "Breakage this month", "Inventory value",
]
