"""Ask OwnPOS (no AI): questions are read into the right report and dates; answers come from report rows."""
from __future__ import annotations

from datetime import date

import pytest

from ownpos.core.ask import answer, parse, parse_period
from tests_pg.helpers import item, receive, sell

TODAY = date(2026, 10, 17)          # a Saturday; the workweek starts Sunday


@pytest.mark.parametrize("text,expect", [
    ("sales today", ("2026-10-17", "2026-10-17")),
    ("yesterday", ("2026-10-16", "2026-10-16")),
    ("this week", ("2026-10-11", "2026-10-17")),
    ("last week", ("2026-10-04", "2026-10-10")),
    ("this month", ("2026-10-01", "2026-10-17")),
    ("last month", ("2026-09-01", "2026-09-30")),
    ("last quarter", ("2026-07-01", "2026-09-30")),
    ("q1 2026", ("2026-01-01", "2026-03-31")),
    ("third quarter", ("2026-07-01", "2026-09-30")),
    ("this year", ("2026-01-01", "2026-10-17")),
    ("last year", ("2025-01-01", "2025-12-31")),
    ("in september", ("2026-09-01", "2026-09-30")),
    ("in november", ("2025-11-01", "2025-11-30")),        # a month still ahead means last year's
    ("december 2025", ("2025-12-01", "2025-12-31")),
    ("last saturday", ("2026-10-10", "2026-10-10")),     # today is Saturday: the one before
    ("friday", ("2026-10-16", "2026-10-16")),
    ("from 10/1 to 10/15", ("2026-10-01", "2026-10-15")),
    ("2026-09-01 - 2026-09-07", ("2026-09-01", "2026-09-07")),
    ("last 30 days", ("2026-09-18", "2026-10-17")),
    ("on 10/5/2026", ("2026-10-05", "2026-10-05")),
])
def test_date_phrases(text, expect):
    a, b, _ = parse_period(text, TODAY, 6)
    assert (a.isoformat(), b.isoformat()) == expect


def test_no_date_phrase():
    assert parse_period("who owes us money", TODAY, 6) is None
    assert parse_period("may we see the margins", TODAY, 6) is None


@pytest.mark.parametrize("text,key", [
    ("Sales today", "sales_summary"),
    ("How did we do last week?", "sales_summary"),
    ("Best sellers in beer this month", "best_sellers"),
    ("What's low on stock?", "reorder"),
    ("Sales tax last quarter", "sales_tax_liability"),
    ("sales tax yesterday", "sales_tax_daily"),
    ("Who owes us money?", "customer_balances"),
    ("Busiest hours last Saturday", "sales_by_hour"),
    ("Sales by cashier yesterday", "sales_by_associate"),
    ("Profit and loss this year", "profit_loss"),
    ("Cash vs card today", "tenders"),
    ("Unpaid bills", "unpaid_bills"),
    ("Hours worked last week", "payroll_hours"),
    ("Breakage this month", "waste"),
    ("Inventory value", "inventory_by_department"),
    ("returns yesterday", "returns"),
    ("balance sheet", "balance_sheet"),
    ("what is the weather", None),
])
def test_questions_pick_the_report(s, text, key):
    p = parse(s.db, text, TODAY, 6)
    assert (p.report if p else None) == key


def test_department_and_item_found(s):
    iid = item(s, "Tito's Handmade Vodka", 2499, size="750ML", department="Liquor")
    p = parse(s.db, "best sellers in beer last week", TODAY, 6)
    assert p.params.get("department_id") == s.db.val("SELECT id FROM departments WHERE name = 'Beer'")
    p = parse(s.db, "How many Tito's did we sell in September?", TODAY, 6)
    assert p.report == "sales_by_item_detail" and p.params["item_id"] == iid
    assert (p.params["date_from"], p.params["date_to"]) == ("2026-09-01", "2026-09-30")


def test_answer_numbers_come_from_the_report(s):
    iid = item(s, "Lager 12OZ", 200)
    receive(s, iid, 50, 100)
    sell(s, [(iid, 3, None)])
    sell(s, [(iid, 2, None)])
    a = answer(s.db, "sales today", s.owner)
    assert a.report == "sales_summary"
    assert "Net sales $10.00" in a.text and "2 receipts" in a.text and "sales tax $0.70" in a.text
    a = answer(s.db, "how many lager did we sell today", s.owner)
    assert a.report == "sales_by_item_detail" and "5 sold" in a.text


def test_answer_respects_permissions(s):
    uid = s.auth.create_user("c9", "Casey", "cashier", pin="5151")
    a = answer(s.db, "profit and loss this month", s.auth.get(uid))
    assert "may not see" in a.text and a.result is None
