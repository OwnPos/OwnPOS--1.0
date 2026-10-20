"""Double-entry ledger. Every document posts one balanced journal entry.
Debits always equal credits, or the posting is refused and the whole
document is rolled back."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .db import Database, now_str

DEBIT_NORMAL = {"asset", "cogs", "expense", "contra_income"}
DRAWER_ACCOUNT = "1000"   # cash in the register drawer


# entries that may touch the drawer account without an open drawer: the Z-Out itself (posted with its
# shift) and opening balances (the float already sitting in the drawers on the first day)
DRAWER_FREE_DOCS = ("ZOUT", "OB")


class DrawerClosed(ValueError):
    pass


class UnbalancedEntry(Exception):
    pass


@dataclass
class Line:
    account: str
    debit: int = 0
    credit: int = 0
    party_type: str | None = None
    party_id: int | None = None


class Ledger:
    def __init__(self, db: Database):
        self.db = db

    def post(self, entry_date: str, doc_type: str, doc_id, doc_no: str | None, memo: str,
             lines: list[Line], user_id=None, shift_id: int | None = None) -> int | None:
        """Post one balanced entry.

        Every entry is tagged with a drawer shift (table journal_shifts): the shift_id
        passed by the caller, or else the shift open on this station at the time of posting. The shift's
        expected drawer cash is derived from the tagged movements on the cash drawer
        account (1000), so purchases, supplier payments, expenses or transfers paid
        from the drawer while a shift is open are always counted, exactly once."""
        # merge lines per account/party and drop zero lines
        merged: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
        for ln in lines:
            if ln.debit < 0 or ln.credit < 0:
                # a negative debit is a credit and vice versa
                d, c = max(ln.debit, 0) + max(-ln.credit, 0), max(ln.credit, 0) + max(-ln.debit, 0)
            else:
                d, c = ln.debit, ln.credit
            key = (ln.account, ln.party_type, ln.party_id)
            merged[key][0] += int(d)
            merged[key][1] += int(c)
        final = []
        for (acct, pt, pid), (d, c) in merged.items():
            net = d - c
            if net:
                final.append(Line(acct, net if net > 0 else 0, -net if net < 0 else 0, pt, pid))
        if not final:
            return None
        td = sum(x.debit for x in final)
        tc = sum(x.credit for x in final)
        if td != tc:
            raise UnbalancedEntry(f"{doc_type} {doc_no}: debits {td} != credits {tc}")
        with self.db.tx():
            codes = sorted({x.account for x in final})
            known = {r[0] for r in self.db.all("SELECT code FROM accounts WHERE code = ANY(?)", (codes,))}
            for code in codes:
                if code not in known:
                    raise ValueError(f"Account {code} does not exist")
            jid = self.db.insert("INSERT INTO journal(ts, entry_date, doc_type, doc_id, doc_no, memo, user_id)"
                                 " VALUES(?,?,?,?,?,?,?)",
                                 (now_str(), entry_date, doc_type, doc_id, doc_no, memo, user_id))
            for x in final:
                self.db.exec("INSERT INTO journal_lines(journal_id, account, debit, credit, party_type, party_id)"
                             " VALUES(?,?,?,?,?,?)", (jid, x.account, x.debit, x.credit, x.party_type, x.party_id))
            if shift_id is None and self.db.station_id is not None:
                # the drawer open on THIS station (each station has its own drawer)
                shift_id = self.db.val("SELECT id FROM shifts WHERE status = 'open' AND station_id = ?",
                                       (self.db.station_id,))
            if shift_id is not None:
                self.db.exec("INSERT INTO journal_shifts(journal_id, shift_id) VALUES(?,?)", (jid, shift_id))
            elif DRAWER_ACCOUNT in codes and doc_type not in DRAWER_FREE_DOCS:
                # cash can only go into or out of a register drawer that is open (and so will be counted at
                # its Z-Out); otherwise the drawer account would drift with nobody's drawer to show it
                raise DrawerClosed("Cash in registers can only be used while a drawer is open on this station. "
                                   "Open the drawer (Make a Sale) or pay from Cash in safe or the checking account.")
        return jid

    def shift_drawer_moves(self, shift_id: int, account: str = DRAWER_ACCOUNT) -> dict[str, int]:
        """Net movement (debit - credit) on the drawer account per doc_type for entries tagged with the shift."""
        rows = self.db.all(
            "SELECT j.doc_type, COALESCE(SUM(l.debit),0) - COALESCE(SUM(l.credit),0) AS amt"
            " FROM journal_shifts js JOIN journal j ON j.id = js.journal_id"
            " JOIN journal_lines l ON l.journal_id = j.id"
            " WHERE js.shift_id = ? AND l.account = ? GROUP BY j.doc_type", (shift_id, account))
        return {r["doc_type"]: int(r["amt"]) for r in rows}

    # ---- balances ----------------------------------------------------
    def balance(self, account: str, date_to: str | None = None, date_from: str | None = None) -> int:
        """Signed balance in the account's normal direction."""
        typ = self.db.val("SELECT type FROM accounts WHERE code = ?", (account,))
        sql = ("SELECT COALESCE(SUM(l.debit),0), COALESCE(SUM(l.credit),0) FROM journal_lines l"
               " JOIN journal j ON j.id = l.journal_id WHERE l.account = ?")
        params: list = [account]
        if date_from:
            sql += " AND j.entry_date >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND j.entry_date <= ?"
            params.append(date_to)
        d, c = self.db.one(sql, params)
        return d - c if typ in DEBIT_NORMAL else c - d

    def party_balance(self, party_type: str, party_id: int, account: str) -> int:
        d, c = self.db.one("SELECT COALESCE(SUM(debit),0), COALESCE(SUM(credit),0) FROM journal_lines"
                           " WHERE party_type = ? AND party_id = ? AND account = ?",
                           (party_type, party_id, account))
        typ = self.db.val("SELECT type FROM accounts WHERE code = ?", (account,))
        return d - c if typ in DEBIT_NORMAL else c - d

    def trial_balance(self, date_to: str | None = None) -> list[dict]:
        rows = []
        for r in self.db.all(self._tb_sql(date_to), [date_to] if date_to else []):
            net = r["d"] - r["c"]
            if r["d"] == 0 and r["c"] == 0:
                continue
            rows.append({"code": r["code"], "name": r["name"], "type": r["type"],
                         "debit": net if net > 0 else 0, "credit": -net if net < 0 else 0})
        return rows

    @staticmethod
    def _tb_sql(date_to):
        cond = "AND j.entry_date <= ?" if date_to else ""
        return ("SELECT a.code, a.name, a.type,"
                f" COALESCE(SUM(CASE WHEN j.id IS NOT NULL THEN l.debit END),0) AS d,"
                f" COALESCE(SUM(CASE WHEN j.id IS NOT NULL THEN l.credit END),0) AS c"
                " FROM accounts a"
                " LEFT JOIN journal_lines l ON l.account = a.code"
                f" LEFT JOIN journal j ON j.id = l.journal_id {cond}"
                " GROUP BY a.code, a.name, a.type ORDER BY a.code")

    def _period_by_type(self, date_from: str, date_to: str) -> list[dict]:
        rows = self.db.all(
            "SELECT a.code, a.name, a.type, COALESCE(SUM(l.debit),0) AS d, COALESCE(SUM(l.credit),0) AS c"
            " FROM journal_lines l JOIN journal j ON j.id = l.journal_id JOIN accounts a ON a.code = l.account"
            " WHERE j.entry_date BETWEEN ? AND ? GROUP BY a.code, a.name, a.type ORDER BY a.code", (date_from, date_to))
        out = []
        for r in rows:
            amt = r["d"] - r["c"] if r["type"] in DEBIT_NORMAL else r["c"] - r["d"]
            out.append({"code": r["code"], "name": r["name"], "type": r["type"], "amount": amt})
        return out

    def profit_and_loss(self, date_from: str, date_to: str) -> dict:
        rows = self._period_by_type(date_from, date_to)
        income = [r for r in rows if r["type"] == "income"]
        contra = [r for r in rows if r["type"] == "contra_income"]
        cogs = [r for r in rows if r["type"] == "cogs"]
        expense = [r for r in rows if r["type"] == "expense"]
        total_income = sum(r["amount"] for r in income) - sum(r["amount"] for r in contra)
        total_cogs = sum(r["amount"] for r in cogs)
        gross = total_income - total_cogs
        total_exp = sum(r["amount"] for r in expense)
        return {"income": income, "contra": contra, "cogs": cogs, "expense": expense,
                "total_income": total_income, "total_cogs": total_cogs, "gross_profit": gross,
                "total_expense": total_exp, "net_profit": gross - total_exp}

    def balance_sheet(self, date_to: str) -> dict:
        tb = self.trial_balance(date_to)
        assets, liabilities, equity = [], [], []
        earnings = 0
        for r in tb:
            net_debit = r["debit"] - r["credit"]
            if r["type"] == "asset":
                assets.append({**r, "amount": net_debit})
            elif r["type"] == "liability":
                liabilities.append({**r, "amount": -net_debit})
            elif r["type"] == "equity":
                equity.append({**r, "amount": -net_debit})
            else:
                earnings += -net_debit  # income positive, costs negative
        ta = sum(r["amount"] for r in assets)
        tl = sum(r["amount"] for r in liabilities)
        te = sum(r["amount"] for r in equity) + earnings
        return {"assets": assets, "liabilities": liabilities, "equity": equity,
                "current_earnings": earnings, "total_assets": ta, "total_liabilities": tl,
                "total_equity": te, "balanced": ta == tl + te}

    def general_ledger(self, account: str, date_from: str, date_to: str) -> list[dict]:
        opening = self.balance_raw(account, before=date_from)
        rows = self.db.all(
            "SELECT j.entry_date, j.doc_type, j.doc_no, j.memo, l.debit, l.credit FROM journal_lines l"
            " JOIN journal j ON j.id = l.journal_id WHERE l.account = ? AND j.entry_date BETWEEN ? AND ?"
            " ORDER BY j.entry_date, j.id", (account, date_from, date_to))
        out = [{"date": date_from, "doc_type": "", "doc_no": "", "memo": "Opening balance",
                "debit": 0, "credit": 0, "balance": opening}]
        run = opening
        for r in rows:
            run += r["debit"] - r["credit"]
            out.append({"date": r["entry_date"], "doc_type": r["doc_type"], "doc_no": r["doc_no"],
                        "memo": r["memo"], "debit": r["debit"], "credit": r["credit"], "balance": run})
        return out

    def balance_raw(self, account: str, before: str) -> int:
        d, c = self.db.one("SELECT COALESCE(SUM(l.debit),0), COALESCE(SUM(l.credit),0) FROM journal_lines l"
                           " JOIN journal j ON j.id = l.journal_id WHERE l.account = ? AND j.entry_date < ?",
                           (account, before))
        return d - c

    def is_balanced(self) -> bool:
        d, c = self.db.one("SELECT COALESCE(SUM(debit),0), COALESCE(SUM(credit),0) FROM journal_lines")
        return d == c
