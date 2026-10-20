"""Alcohol sale hours: a WARNING only (decision 16 Oct 2026), never a block, because a wrong computer clock
must never stop the store. The cashier can continue; the sale then records that it was made outside hours.

Hours are per weekday in Settings (Indiana carryout defaults in seed.py: Mon-Sat 7:00 am - 3:00 am the next
morning, Sunday noon - 8:00 pm; see docs/RESEARCH.md). A closing time earlier than the opening time means
"after midnight" (03:00 = 3 am the next morning). The time used is the server clock."""
from __future__ import annotations

from datetime import datetime, time, timedelta

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _t(text: str) -> time:
    h, m = text.strip().split(":")
    return time(int(h), int(m))


def windows_for(hours: dict, day: datetime) -> list[tuple[datetime, datetime]]:
    """Selling windows that touch this calendar day: its own window and the previous day's after-midnight part."""
    out = []
    for offset in (-1, 0):
        d = (day + timedelta(days=offset)).date()
        spec = hours.get(str(d.weekday()))
        if not spec or len(spec) != 2 or not spec[0] or not spec[1]:
            continue
        start = datetime.combine(d, _t(spec[0]))
        end = datetime.combine(d, _t(spec[1]))
        if end <= start:
            end += timedelta(days=1)
        out.append((start, end))
    return out


def is_open(hours: dict, when: datetime) -> bool:
    return any(start <= when < end for start, end in windows_for(hours, when))


def describe(hours: dict) -> str:
    def fmt(t):
        return _t(t).strftime("%I:%M %p").lstrip("0")
    parts = []
    for i, name in enumerate(DAYS):
        spec = hours.get(str(i))
        if not spec or not spec[0]:
            parts.append(f"{name}: no sales")
            continue
        late = _t(spec[1]) <= _t(spec[0])
        parts.append(f"{name}: {fmt(spec[0])} - {fmt(spec[1])}{' (next morning)' if late else ''}")
    return "; ".join(parts)


def check_sale_time(db, when: datetime, department_id: int | None = None) -> str | None:
    """None when selling is fine now, else the warning text to show the cashier."""
    if not db.get_setting("sale_hours_check", True):
        return None
    limited = db.get_setting("sale_hours_departments", []) or []
    if limited and department_id is not None and int(department_id) not in {int(x) for x in limited}:
        return None
    hours = db.get_setting("sale_hours", {}) or {}
    if not hours or is_open(hours, when):
        return None
    return (f"It is {when:%A %I:%M %p} by the store clock, which is outside the alcohol sale hours set in "
            f"Settings ({describe(hours)}). If the clock is wrong or this item may be sold now, press Continue; "
            "the sale will be marked as made outside hours.")
