"""Stations (the store's POS computers). Each has a number; the number decides which drawer it uses.

A station registers itself when it connects. The licence decides how many stations may sell; an extra
station can still open reports and settings but cannot sell (see licence.py)."""
from __future__ import annotations

import platform

from .db import Database, now_str


class StationError(Exception):
    pass


def register(db: Database, station_id: int, name: str = "", computer: str | None = None) -> dict:
    """Record this station and refresh its 'last seen'. Returns {'can_sell': bool, 'message': str}."""
    from .licence import allowed_stations
    if station_id is None or int(station_id) < 1:
        raise StationError("The station number must be 1 or more")
    computer = computer if computer is not None else platform.node()
    with db.tx():
        row = db.one("SELECT * FROM stations WHERE id = ?", (station_id,))
        if row is None:
            db.exec("INSERT INTO stations(id, name, computer, first_seen, last_seen) VALUES(?,?,?,?,?)",
                    (station_id, name or f"Station {station_id}", computer, now_str(), now_str()))
        else:
            db.exec("UPDATE stations SET last_seen = ?, computer = ? WHERE id = ?", (now_str(), computer, station_id))
            if row["computer"] and computer and row["computer"] != computer:
                from .auth import audit
                audit(db, None, "station_computer_changed", "station", station_id,
                      {"was": row["computer"], "now": computer})
    limit = allowed_stations(db)
    if limit is None:
        return {"can_sell": True, "message": ""}
    active = [r["id"] for r in db.all("SELECT id FROM stations WHERE active = 1 ORDER BY id")]
    if station_id in active[:limit]:
        return {"can_sell": True, "message": ""}
    return {"can_sell": False,
            "message": f"The licence allows {limit} station(s). Stations {', '.join(map(str, active[:limit]))} are "
                       f"the licensed ones. Turn one off in Settings > Stations, or ask for a bigger licence."}


def stations(db: Database) -> list[dict]:
    return db.dicts("SELECT * FROM stations ORDER BY id")


def set_active(db: Database, station_id: int, active: bool, user_id: int) -> None:
    from .auth import audit
    with db.tx():
        if not active and db.val("SELECT 1 FROM shifts WHERE station_id = ? AND status = 'open'", (station_id,)):
            raise StationError("Close this station's drawer (Z-Out) before turning it off")
        db.exec("UPDATE stations SET active = ? WHERE id = ?", (1 if active else 0, station_id))
        audit(db, user_id, "station_active" if active else "station_inactive", "station", station_id, {})
