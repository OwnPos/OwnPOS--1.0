"""Drawer shifts (one per station), drawer cash movements, the X-Out / Z-Out reports and the store's
Day Closing report (all stations together, with the day's sales tax)."""
from __future__ import annotations

import json

from .auth import audit
from .db import Database, now_str, today_str
from .ledger import Ledger, Line


class ShiftError(Exception):
    pass


# Drawer lines on the X/Z-Out, built from the shift's movements on the cash drawer
# account (1000) grouped by document type. (key, label, doc types, sign shown on
# the summary dict: +1 = money in, -1 = money out shown as a positive amount).
DRAWER_GROUPS = [
    ("cash_sales_net", "Cash sales (net)", ("INV", "CN"), 1),
    ("account_payments_cash", "Cash payments on account", ("PAY",), 1),
    ("paid_in", "Paid in", ("PAIDIN",), 1),
    ("payouts", "Payouts", ("PAYOUT",), -1),
    ("drops", "Drops to bank / safe", ("DROP",), -1),
    ("supplier_payments_cash", "Paid to vendors", ("RV", "VPAY"), -1),
    ("vendor_refunds_cash", "Cash refunds from vendors", ("VR",), 1),
    ("expenses_cash", "Expenses paid from drawer", ("EX",), -1),
]
# Not part of the shift's drawer cash: the Z-Out over/short itself, and opening
# balances (cash that was already in the drawer, i.e. inside the opening float).
DRAWER_EXCLUDED = ("ZOUT", "OB")


class ShiftService:
    def __init__(self, db: Database, ledger: Ledger):
        self.db = db
        self.ledger = ledger

    def _station(self, station_id: int | None) -> int:
        sid = station_id if station_id is not None else self.db.station_id
        if sid is None:
            raise ShiftError("This computer has no station number. Set it in Settings > This station.")
        return sid

    def current(self, station_id: int | None = None):
        """The drawer open on this station (each station has its own drawer)."""
        sid = station_id if station_id is not None else self.db.station_id
        if sid is None:
            return None
        return self.db.one("SELECT * FROM shifts WHERE status = 'open' AND station_id = ?", (sid,))

    def open_shifts(self) -> list:
        return self.db.all("SELECT s.*, u.full_name, st.name AS station_name FROM shifts s"
                           " JOIN users u ON u.id = s.user_id LEFT JOIN stations st ON st.id = s.station_id"
                           " WHERE s.status = 'open' ORDER BY s.station_id")

    def open(self, user_id: int, opening_float: int, notes: str = "", station_id: int | None = None) -> int:
        station = self._station(station_id)
        if opening_float < 0:
            raise ShiftError("The opening cash cannot be negative")
        with self.db.tx():
            if self.current(station):
                raise ShiftError("This station's drawer is already open. Close it with a Z-Out first.")
            sid = self.db.insert("INSERT INTO shifts(station_id, user_id, opened_at, opening_float, notes)"
                                 " VALUES(?,?,?,?,?)", (station, user_id, now_str(), int(opening_float), notes or None))
            audit(self.db, user_id, "shift_open", "shift", sid, {"float": opening_float, "station": station})
        return sid

    # ---- cash in and out of the drawer --------------------------------
    def cash_move(self, kind: str, amount: int, user_id: int, reason: str = "", account: str | None = None,
                  note: str = "") -> int:
        """kind: payout (expense paid from drawer), drop (cash to bank/safe), paid_in, no_sale."""
        shift = self.current()
        if shift is None and kind != "no_sale":
            raise ShiftError("Open this station's drawer first")
        if kind != "no_sale" and amount <= 0:
            raise ShiftError("Amount must be more than zero")
        with self.db.tx():
            mid = self.db.insert("INSERT INTO cash_moves(created_at, station_id, shift_id, user_id, kind, amount, account,"
                                 " reason, note) VALUES(?,?,?,?,?,?,?,?,?)",
                                 (now_str(), self.db.station_id, shift["id"] if shift else None, user_id, kind, amount,
                                  account, reason, note))
            if kind == "payout":
                self.ledger.post(today_str(), "PAYOUT", mid, f"PO-{mid}", f"Payout: {reason} {note}".strip(),
                                 [Line(account or "6900", debit=amount), Line("1000", credit=amount)], user_id,
                                 shift_id=shift["id"])
            elif kind == "drop":
                self.ledger.post(today_str(), "DROP", mid, f"DR-{mid}", f"Cash drop to {account or 'bank'}",
                                 [Line(account or "1100", debit=amount), Line("1000", credit=amount)], user_id,
                                 shift_id=shift["id"])
            elif kind == "paid_in":
                self.ledger.post(today_str(), "PAIDIN", mid, f"PI-{mid}", f"Paid in: {reason}",
                                 [Line("1000", debit=amount), Line(account or "1010", credit=amount)], user_id,
                                 shift_id=shift["id"])
            audit(self.db, user_id, kind, "cash_move", mid, {"amount": amount, "reason": reason})
        return mid

    # ---- reports -----------------------------------------------------
    def summary(self, shift_id: int) -> dict:
        """Everything the X-Out and Z-Out show for one shift."""
        s = self.db.one("SELECT * FROM shifts WHERE id = ?", (shift_id,))
        if s is None:
            raise ShiftError("Shift not found")
        inv = self.db.one("SELECT COUNT(*) n, COALESCE(SUM(total),0) total, COALESCE(SUM(tax),0) tax,"
                          " COALESCE(SUM(discount),0) disc, COALESCE(SUM(subtotal),0) gross,"
                          " COALESCE(SUM(nontaxable),0) nontaxable,"
                          " COALESCE(SUM(taxable),0) taxable, COALESCE(SUM(cost_total),0) cost"
                          " FROM sales WHERE shift_id = ? AND doc_type = 'INV'", (shift_id,))
        cn = self.db.one("SELECT COUNT(*) n, COALESCE(SUM(total),0) total, COALESCE(SUM(tax),0) tax,"
                         " COALESCE(SUM(taxable),0) taxable, COALESCE(SUM(nontaxable),0) nontaxable"
                         " FROM sales WHERE shift_id = ? AND doc_type = 'CN'", (shift_id,))
        tenders = self.db.dicts(
            "SELECT t.method_code, m.name, s.doc_type, COALESCE(SUM(t.amount),0) amount, COUNT(*) n"
            " FROM sale_tenders t JOIN sales s ON s.id = t.sale_id LEFT JOIN payment_methods m ON m.code = t.method_code"
            " WHERE s.shift_id = ? GROUP BY t.method_code, m.name, m.sort, s.doc_type ORDER BY m.sort", (shift_id,))
        by_method: dict[str, dict] = {}
        for t in tenders:
            row = by_method.setdefault(t["method_code"], {"code": t["method_code"], "name": t["name"] or t["method_code"],
                                                          "sales": 0, "refunds": 0})
            if t["doc_type"] == "INV":
                row["sales"] += t["amount"]
            else:
                row["refunds"] += t["amount"]
        for row in by_method.values():
            row["net"] = row["sales"] - row["refunds"]
        moves = self.db.dicts("SELECT kind, COALESCE(SUM(amount),0) amount, COUNT(*) n FROM cash_moves"
                              " WHERE shift_id = ? GROUP BY kind", (shift_id,))
        mv = {m["kind"]: m for m in moves}
        drawer = self._drawer(s, by_method, mv)
        expected = s["opening_float"] + sum(ln["amount"] for ln in drawer["lines"])
        voids = self.db.val("SELECT COUNT(*) FROM audit_log WHERE action = 'void_line' AND station_id = ?"
                            " AND ts >= ? AND ts <= ?", (s["station_id"], s["opened_at"], s["closed_at"] or "9999"), 0)
        discounts = self.db.dicts(
            "SELECT COALESCE(discount_reason, 'Other discounts') reason, COUNT(*) n, COALESCE(SUM(discount),0) amount"
            " FROM sales WHERE shift_id = ? AND doc_type = 'INV' AND discount > 0"
            " GROUP BY COALESCE(discount_reason, 'Other discounts') ORDER BY 1", (shift_id,))
        out = {
            "shift": dict(s), "invoices": inv["n"], "sales_total": inv["total"],
            "tax": inv["tax"] - cn["tax"], "tax_sales": inv["tax"], "tax_returns": cn["tax"],
            "gross": inv["gross"], "discounts": inv["disc"], "discount_lines": discounts,
            "nontaxable": inv["nontaxable"] - cn["nontaxable"], "taxable": inv["taxable"] - cn["taxable"],
            "cost": inv["cost"], "returns_count": cn["n"], "returns_total": cn["total"],
            "net_sales": inv["total"] - cn["total"], "tenders": list(by_method.values()),
            "no_sales": mv.get("no_sale", {}).get("n", 0), "voids": voids,
            # every movement of drawer cash in the shift, in print order; amount is signed (+ in, - out)
            "drawer_lines": [{"key": "opening_float", "label": "Opening float", "amount": s["opening_float"]}]
                            + drawer["lines"],
            "expected_cash": expected,
        }
        out.update(drawer["values"])
        return out

    def _drawer(self, s, by_method: dict, mv: dict) -> dict:
        """Drawer cash movements of the shift.

        Source of truth is the ledger: every entry posted on this station while its drawer is open is tagged
        with the shift (journal_shifts), so its net movement on the drawer account 1000 is exactly the cash that
        went in or out - sales, refunds, payments on account, payouts, drops, paid-ins, and vendor payments or
        expenses paid from the drawer."""
        values = {g[0]: 0 for g in DRAWER_GROUPS}
        moves = self.ledger.shift_drawer_moves(s["id"])
        grouped = set()
        for key, _label, types, sign in DRAWER_GROUPS:
            values[key] = sign * sum(moves.get(t, 0) for t in types)
            grouped.update(types)
        values["other_cash"] = sum(a for t, a in moves.items() if t not in grouped and t not in DRAWER_EXCLUDED)
        lines = [{"key": key, "label": label, "amount": sign * values[key]} for key, label, _t, sign in DRAWER_GROUPS]
        lines.append({"key": "other_cash", "label": "Other drawer movements", "amount": values["other_cash"]})
        return {"values": values, "lines": lines}

    def close(self, user_id: int, counted_cash: int, counts: dict | None = None, notes: str = "",
              station_id: int | None = None) -> dict:
        """Z-Out: count this station's drawer, post any over/short, and close the shift."""
        if counted_cash < 0:
            raise ShiftError("The counted cash cannot be negative")
        station = self._station(station_id)
        with self.db.tx():
            shift = self.current(station)              # read and summed under the write lock
            if shift is None:
                raise ShiftError("This station's drawer is not open")
            summ = self.summary(shift["id"])
            over_short = int(counted_cash) - summ["expected_cash"]
            z_no = f"Z-{self.db.next_seq('Z')}"
            self.db.exec("UPDATE shifts SET closed_at = ?, expected_cash = ?, counted_cash = ?, over_short = ?,"
                         " counts_json = ?, z_number = ?, status = 'closed', closed_by = ?, notes = COALESCE(?, notes)"
                         " WHERE id = ?",
                         (now_str(), summ["expected_cash"], counted_cash, over_short,
                          json.dumps(counts or {}), z_no, user_id, notes or None, shift["id"]))
            if over_short:
                lines = ([Line("1000", debit=over_short), Line("6600", credit=over_short)] if over_short > 0
                         else [Line("6600", debit=-over_short), Line("1000", credit=-over_short)])
                self.ledger.post(today_str(), "ZOUT", shift["id"], z_no, f"Cash over/short at {z_no}", lines, user_id,
                                 shift_id=shift["id"])
            audit(self.db, user_id, "z_out", "shift", shift["id"],
                  {"z": z_no, "station": station, "expected": summ["expected_cash"], "counted": counted_cash,
                   "over_short": over_short})
        summ["over_short"] = over_short
        summ["counted_cash"] = counted_cash
        summ["z_number"] = z_no
        summ["shift"] = dict(self.db.one("SELECT * FROM shifts WHERE id = ?", (shift["id"],)))
        return summ

    # ---- the store's Day Closing report -------------------------------------------------------
    def day_closing(self, day: str) -> dict:
        """All stations together for one store date: sales, returns, discounts, sales tax, payments by method,
        each station's drawer (Z-Outs) and anything still open. Sales tax here is what the receipts of that
        date charged (sales minus returns), which is what the quarterly ST-103 return adds up."""
        tot = self.db.one(
            "SELECT COUNT(*) FILTER (WHERE doc_type='INV') n_inv, COUNT(*) FILTER (WHERE doc_type='CN') n_cn,"
            " COALESCE(SUM(CASE WHEN doc_type='INV' THEN subtotal END),0) gross,"
            " COALESCE(SUM(CASE WHEN doc_type='INV' THEN discount END),0) disc,"
            " COALESCE(SUM(CASE WHEN doc_type='INV' THEN taxable+nontaxable ELSE -(taxable+nontaxable) END),0) net,"
            " COALESCE(SUM(CASE WHEN doc_type='INV' THEN taxable ELSE -taxable END),0) taxable,"
            " COALESCE(SUM(CASE WHEN doc_type='INV' THEN nontaxable ELSE -nontaxable END),0) nontaxable,"
            " COALESCE(SUM(CASE WHEN doc_type='INV' THEN tax ELSE -tax END),0) tax,"
            " COALESCE(SUM(CASE WHEN doc_type='INV' THEN total END),0) sales_total,"
            " COALESCE(SUM(CASE WHEN doc_type='CN' THEN total END),0) returns_total,"
            " COALESCE(SUM(CASE WHEN doc_type='INV' THEN cost_total ELSE -cost_total END),0) cost"
            " FROM sales WHERE sale_date = ?", (day,))
        tenders = self.db.dicts(
            "SELECT t.method_code code, COALESCE(m.name, t.method_code) AS name,"
            " COALESCE(SUM(CASE WHEN s.doc_type='INV' THEN t.amount END),0) sales,"
            " COALESCE(SUM(CASE WHEN s.doc_type='CN' THEN t.amount END),0) refunds"
            " FROM sale_tenders t JOIN sales s ON s.id = t.sale_id LEFT JOIN payment_methods m ON m.code = t.method_code"
            " WHERE s.sale_date = ? GROUP BY t.method_code, m.name, m.sort ORDER BY m.sort", (day,))
        for t in tenders:
            t["net"] = t["sales"] - t["refunds"]
        stations = self.db.dicts(
            "SELECT s.id, s.station_id, st.name AS station_name, u.full_name AS cashier, s.opened_at, s.closed_at,"
            " s.status, s.z_number, s.opening_float, s.expected_cash, s.counted_cash, s.over_short"
            " FROM shifts s JOIN users u ON u.id = s.user_id LEFT JOIN stations st ON st.id = s.station_id"
            " WHERE substr(s.opened_at, 1, 10) = ? OR substr(COALESCE(s.closed_at, ''), 1, 10) = ?"
            " OR (s.status = 'open' AND substr(s.opened_at, 1, 10) <= ?)"
            " ORDER BY s.station_id, s.opened_at", (day, day, day))
        discounts = self.db.dicts(
            "SELECT COALESCE(discount_reason, 'Other discounts') reason, COUNT(*) n, COALESCE(SUM(discount),0) amount"
            " FROM sales WHERE sale_date = ? AND doc_type = 'INV' AND discount > 0"
            " GROUP BY COALESCE(discount_reason, 'Other discounts') ORDER BY 1", (day,))
        payouts = self.db.dicts(
            "SELECT kind, COUNT(*) n, COALESCE(SUM(amount),0) amount FROM cash_moves"
            " WHERE substr(created_at, 1, 10) = ? GROUP BY kind ORDER BY kind", (day,))
        out = dict(tot)
        out.update({"date": day, "tenders": tenders, "stations": stations, "discount_lines": discounts,
                    "cash_moves": payouts, "open_drawers": [s for s in stations if s["status"] == "open"],
                    "over_short": sum(int(s["over_short"] or 0) for s in stations if s["status"] == "closed")})
        out["gross_profit"] = out["net"] - out["cost"]
        return out
