"""Time clock: employees clock in and out; the owner can fix a missed punch (always with a reason, logged).

Hours report for the accountant: hours per employee per workweek, split into regular hours and overtime
(hours over 40 in a workweek, the federal FLSA rule; see docs/RESEARCH.md), with an optional estimate of
gross pay from each employee's rate. Withholding and payroll taxes are not calculated - the accountant does
that, as before.

Rules used (also printed on the report):
- A punch belongs to the day and workweek in which it was clocked IN (a shift past midnight counts on the
  day it started).
- The workweek starts on the day set in Settings (default Sunday).
- A punch that is still open (employee still clocked in) is listed but not counted until it is closed."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .auth import User, audit
from .db import Database, now_dt, now_str
from .money import round_cents

FMT = "%Y-%m-%d %H:%M:%S"


class TimeClockError(Exception):
    pass


def _parse(text: str) -> datetime:
    try:
        return datetime.strptime(text.strip(), FMT)
    except ValueError:
        try:
            return datetime.strptime(text.strip(), "%Y-%m-%d %H:%M")
        except ValueError as e:
            raise TimeClockError(f"'{text}' is not a date and time like 2026-10-16 17:30") from e


class TimeClock:
    def __init__(self, db: Database):
        self.db = db

    def open_entry(self, user_id: int):
        return self.db.one("SELECT * FROM time_entries WHERE user_id = ? AND clock_out IS NULL", (user_id,))

    def who_is_in(self) -> list:
        return self.db.all("SELECT t.*, u.full_name FROM time_entries t JOIN users u ON u.id = t.user_id"
                           " WHERE t.clock_out IS NULL ORDER BY t.clock_in")

    def clock_in(self, user_id: int, station_id: int | None = None) -> int:
        with self.db.tx():
            if self.open_entry(user_id):
                raise TimeClockError("You are already clocked in")
            tid = self.db.insert("INSERT INTO time_entries(user_id, clock_in, station_id) VALUES(?,?,?)",
                                 (user_id, now_str(), station_id if station_id is not None else self.db.station_id))
            audit(self.db, user_id, "clock_in", "time_entry", tid, {})
        return tid

    def clock_out(self, user_id: int) -> dict:
        with self.db.tx():
            e = self.open_entry(user_id)
            if e is None:
                raise TimeClockError("You are not clocked in")
            out = now_str()
            if _parse(out) < _parse(e["clock_in"]):
                raise TimeClockError("The clock-out time is before the clock-in time; ask the owner to fix the punch")
            self.db.exec("UPDATE time_entries SET clock_out = ? WHERE id = ?", (out, e["id"]))
            hours = (_parse(out) - _parse(e["clock_in"])).total_seconds() / 3600
            audit(self.db, user_id, "clock_out", "time_entry", e["id"], {"hours": round(hours, 2)})
        return {"clock_in": e["clock_in"], "clock_out": out, "hours": hours}

    # ---- corrections (owner / timeclock_edit) -------------------------------------------------
    def _check_editor(self, actor: User) -> None:
        if actor is None or not actor.can("timeclock_edit"):
            raise TimeClockError("You do not have permission to fix time clock punches")

    def _check_overlap(self, user_id: int, start: datetime, end: datetime | None, except_id: int | None) -> None:
        for r in self.db.all("SELECT * FROM time_entries WHERE user_id = ? AND id <> ?", (user_id, except_id or 0)):
            s = _parse(r["clock_in"])
            e = _parse(r["clock_out"]) if r["clock_out"] else datetime.max
            if start < e and (end or datetime.max) > s:
                raise TimeClockError(f"This overlaps another punch ({r['clock_in']} - {r['clock_out'] or 'still in'})")

    def save_entry(self, user_id: int, clock_in: str, clock_out: str | None, reason: str, actor: User,
                   entry_id: int | None = None) -> int:
        """Add a missed punch (entry_id None) or correct one. A reason is required."""
        self._check_editor(actor)
        if not (reason or "").strip():
            raise TimeClockError("Give a reason for the correction")
        start = _parse(clock_in)
        end = _parse(clock_out) if clock_out else None
        if end is not None and end <= start:
            raise TimeClockError("Clock-out must be after clock-in")
        if end is not None and end - start > timedelta(hours=24):
            raise TimeClockError("A single punch cannot be longer than 24 hours")
        if start > now_dt() + timedelta(minutes=5):
            raise TimeClockError("Clock-in cannot be in the future")
        with self.db.tx():
            self._check_overlap(user_id, start, end, entry_id)
            if entry_id is None:
                tid = self.db.insert("INSERT INTO time_entries(user_id, clock_in, clock_out, edited_by, edit_reason)"
                                     " VALUES(?,?,?,?,?)", (user_id, start.strftime(FMT),
                                                            end.strftime(FMT) if end else None, actor.id, reason.strip()))
                audit(self.db, actor.id, "time_add", "time_entry", tid,
                      {"user_id": user_id, "in": start.strftime(FMT), "out": end.strftime(FMT) if end else None,
                       "reason": reason.strip()})
                return tid
            old = self.db.one("SELECT * FROM time_entries WHERE id = ?", (entry_id,))
            if old is None:
                raise TimeClockError("Punch not found")
            self.db.exec("UPDATE time_entries SET clock_in = ?, clock_out = ?, edited_by = ?, edit_reason = ?"
                         " WHERE id = ?", (start.strftime(FMT), end.strftime(FMT) if end else None, actor.id,
                                          reason.strip(), entry_id))
            audit(self.db, actor.id, "time_edit", "time_entry", entry_id,
                  {"user_id": old["user_id"], "old": [old["clock_in"], old["clock_out"]],
                   "new": [start.strftime(FMT), end.strftime(FMT) if end else None], "reason": reason.strip()})
            return entry_id

    # ---- reports ------------------------------------------------------------------------------
    def week_start(self, d: date) -> date:
        first = int(self.db.get_setting("week_starts", 6))        # 0 = Monday ... 6 = Sunday
        return d - timedelta(days=(d.weekday() - first) % 7)

    def entries(self, date_from: str, date_to: str, user_id: int | None = None) -> list[dict]:
        sql = ("SELECT t.*, u.full_name, u.username FROM time_entries t JOIN users u ON u.id = t.user_id"
               " WHERE substr(t.clock_in, 1, 10) BETWEEN ? AND ?")
        params: list = [date_from, date_to]
        if user_id:
            sql += " AND t.user_id = ?"
            params.append(user_id)
        rows = self.db.dicts(sql + " ORDER BY u.full_name, t.clock_in", params)
        for r in rows:
            r["hours"] = (round((_parse(r["clock_out"]) - _parse(r["clock_in"])).total_seconds() / 3600, 2)
                          if r["clock_out"] else None)
        return rows

    def payroll(self, date_from: str, date_to: str, with_pay: bool = False) -> list[dict]:
        """One row per employee per workweek: hours, regular, overtime and (with_pay) estimated gross pay.

        To split overtime correctly, whole workweeks are used: the period is widened to the workweeks it
        touches, and rows show which week they are. Hours are summed exactly (seconds) and shown to 2 decimals."""
        start = self.week_start(date.fromisoformat(date_from))
        end = self.week_start(date.fromisoformat(date_to)) + timedelta(days=6)
        limit = float(self.db.get_setting("overtime_weekly_hours", 40))
        users = {r["id"]: r for r in self.db.all("SELECT * FROM users")}
        weeks: dict[tuple, dict] = {}

        def week_row(e, ws):
            return weeks.setdefault((e["user_id"], ws), {"user_id": e["user_id"], "employee": e["full_name"],
                                                         "week_start": ws.isoformat(), "seconds": 0, "punches": 0,
                                                         "open_punches": 0, "edited": 0})
        # a punch started the day before can run into the first week (a Saturday night shift past midnight)
        for e in self.entries((start - timedelta(days=1)).isoformat(), end.isoformat()):
            t_in = _parse(e["clock_in"])
            if not e["clock_out"]:
                ws = self.week_start(t_in.date())
                if start <= ws <= end:
                    week_row(e, ws)["open_punches"] += 1
                continue
            t_out = _parse(e["clock_out"])
            # hours count in the workweek in which they are worked (FLSA): split a punch at the week boundary
            cur = t_in
            first = True
            while cur < t_out:
                ws = self.week_start(cur.date())
                boundary = datetime.combine(ws + timedelta(days=7), datetime.min.time())
                piece_end = min(t_out, boundary)
                if start <= ws <= end:
                    row = week_row(e, ws)
                    row["seconds"] += (piece_end - cur).total_seconds()
                    if first:
                        row["punches"] += 1
                        if e["edited_by"]:
                            row["edited"] += 1
                first = False
                cur = piece_end
        out = []
        for (uid, _ws), row in sorted(weeks.items(), key=lambda kv: (kv[1]["employee"], kv[1]["week_start"])):
            u = users.get(uid)
            hours = row["seconds"] / 3600
            ot_applies = bool(u and u["overtime"] and u["pay_type"] == "hourly")
            regular = min(hours, limit) if ot_applies else hours
            overtime = max(hours - limit, 0) if ot_applies else 0.0
            r = {"employee": row["employee"], "week_start": row["week_start"],
                 "week_end": (date.fromisoformat(row["week_start"]) + timedelta(days=6)).isoformat(),
                 "hours": round(hours, 2), "regular": round(regular, 2), "overtime": round(overtime, 2),
                 "punches": row["punches"], "open_punches": row["open_punches"], "edited": row["edited"]}
            if with_pay and u is not None:
                rate = int(u["pay_rate"] or 0)
                r["pay_type"] = u["pay_type"]
                r["rate"] = rate
                if u["pay_type"] == "hourly":
                    r["gross_pay"] = round_cents(regular * rate + overtime * rate * 1.5)
                elif u["pay_type"] == "salary":
                    r["gross_pay"] = rate
                else:
                    r["gross_pay"] = 0
            out.append(r)
        return out
