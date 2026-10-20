"""Back-office accounting entries: expenses, transfers (bank deposits, card settlements), manual journals and
opening balances."""
from __future__ import annotations

from .auth import audit
from .db import Database, now_str, today_str
from .ledger import Ledger, Line


class AccountingError(Exception):
    pass


class AccountingService:
    def __init__(self, db: Database, ledger: Ledger):
        self.db = db
        self.ledger = ledger

    def expense(self, account: str, amount: int, paid_from: str, user_id: int,
                expense_date: str | None = None, payee: str = "", vendor_id: int | None = None,
                ref: str = "", note: str = "") -> int:
        """An expense paid (or owed, paid_from='2000' with a vendor). amount in cents."""
        if amount <= 0:
            raise AccountingError("Amount must be more than zero")
        if paid_from == "2000" and not vendor_id:
            raise AccountingError("Choose the vendor the bill is owed to")
        expense_date = expense_date or today_str()
        with self.db.tx():
            doc_no = f"EX-{self.db.next_seq('EX'):05d}"
            eid = self.db.insert("INSERT INTO expenses(doc_no, created_at, expense_date, account, vendor_id, payee, amount,"
                                 " paid_from, ref, user_id, note) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                 (doc_no, now_str(), expense_date, account, vendor_id, payee or None, amount,
                                  paid_from, ref or None, user_id, note or None))
            lines = [Line(account, debit=amount)]
            party = ("vendor", vendor_id) if paid_from == "2000" and vendor_id else (None, None)
            lines.append(Line(paid_from, credit=amount, party_type=party[0], party_id=party[1]))
            self.ledger.post(expense_date, "EX", eid, doc_no, f"Expense: {payee or note or account}", lines, user_id)
            audit(self.db, user_id, "expense", "expense", eid, {"doc_no": doc_no, "amount": amount, "account": account})
        return eid

    def transfer(self, from_account: str, to_account: str, amount: int, user_id: int, fee: int = 0,
                 memo: str = "Transfer", entry_date: str | None = None) -> int:
        """Move money between accounts, e.g. card settlement to the checking account with the processing fee,
        or a cash deposit from the safe to the bank. fee goes to 6400 Card processing fees."""
        if amount <= 0:
            raise AccountingError("Amount must be more than zero")
        lines = [Line(to_account, debit=amount - fee), Line(from_account, credit=amount)]
        if fee < 0 or fee >= amount:
            raise AccountingError("The fee must be less than the amount")
        if fee:
            lines.append(Line("6400", debit=fee))
        with self.db.tx():
            jid = self.ledger.post(entry_date or today_str(), "TRF", None, None, memo, lines, user_id)
            audit(self.db, user_id, "transfer", "journal", jid, {"from": from_account, "to": to_account, "amount": amount})
        return jid

    def pay_sales_tax(self, amount: int, paid_from: str, user_id: int, pay_date: str | None = None,
                      allowance: int = 0, period: str = "", ref: str = "") -> int:
        """Pay the Indiana sales tax return. amount: what was paid to the Department of Revenue; allowance: the
        part of the tax collected that the return lets the store keep (collection allowance, rounding), as worked
        out on the filed return. Both reduce 2100 Sales tax payable; the allowance is income (4800)."""
        if amount < 0 or allowance < 0 or amount + allowance <= 0:
            raise AccountingError("Enter the amount paid (and any allowance kept)")
        if amount and not paid_from:
            raise AccountingError("Choose the account the tax was paid from")
        lines = [Line("2100", debit=amount + allowance)]
        if amount:
            lines.append(Line(paid_from, credit=amount))
        if allowance:
            lines.append(Line("4800", credit=allowance))
        memo = "Indiana sales tax paid" + (f" for {period}" if period else "") + (f" (ref {ref})" if ref else "")
        with self.db.tx():
            seq = self.db.next_seq("STP")
            jid = self.ledger.post(pay_date or today_str(), "STPAY", seq, f"STP-{seq:05d}", memo, lines, user_id)
            audit(self.db, user_id, "sales_tax_payment", "journal", jid,
                  {"amount": amount, "allowance": allowance, "period": period, "ref": ref})
        return jid

    def manual_journal(self, lines: list[dict], memo: str, user_id: int, entry_date: str | None = None) -> int:
        """lines: [{'account', 'debit', 'credit'}]. Must balance."""
        if not memo:
            raise AccountingError("A memo is required for a manual entry")
        jl = [Line(ln["account"], debit=int(ln.get("debit") or 0), credit=int(ln.get("credit") or 0)) for ln in lines]
        with self.db.tx():
            seq = self.db.next_seq("JE")
            jid = self.ledger.post(entry_date or today_str(), "JE", seq, f"JE-{seq:05d}", memo, jl, user_id)
            audit(self.db, user_id, "manual_journal", "journal", jid, {"memo": memo})
        return jid

    def opening_balance(self, account: str, amount: int, user_id: int, entry_date: str | None = None) -> int:
        """Opening cash, bank, receivable or payable balance against opening balance equity."""
        typ = self.db.val("SELECT type FROM accounts WHERE code = ?", (account,))
        if typ is None:
            raise AccountingError("Unknown account")
        if not amount:
            raise AccountingError("Amount must not be zero")
        if typ == "asset":
            lines = [Line(account, debit=amount), Line("3100", credit=amount)]
        else:
            lines = [Line("3100", debit=amount), Line(account, credit=amount)]
        with self.db.tx():
            jid = self.ledger.post(entry_date or today_str(), "OB", None, "OPENING", f"Opening balance {account}",
                                   lines, user_id)
            audit(self.db, user_id, "opening_balance", "account", account, {"amount": amount})
        return jid

    def party_opening_balance(self, party_type: str, party_id: int, amount: int, user_id: int,
                              entry_date: str | None = None, memo: str = "") -> int:
        """What one customer owed us (or one supplier was owed) on the day the store started using OwnPOS.
        customer: amount > 0 means the customer owes us (house charge); vendor: amount > 0 means we owe the supplier.
        A negative amount is an advance the other way round. The other side is opening balance equity (3100)."""
        if not amount:
            raise AccountingError("Amount must not be zero")
        if party_type == "customer":
            acct = "1200"
            lines = ([Line(acct, debit=amount, party_type="customer", party_id=party_id), Line("3100", credit=amount)]
                     if amount > 0 else
                     [Line("3100", debit=-amount), Line(acct, credit=-amount, party_type="customer", party_id=party_id)])
        elif party_type == "vendor":
            acct = "2000"
            lines = ([Line("3100", debit=amount), Line(acct, credit=amount, party_type="vendor", party_id=party_id)]
                     if amount > 0 else
                     [Line(acct, debit=-amount, party_type="vendor", party_id=party_id), Line("3100", credit=-amount)])
        else:
            raise AccountingError("Opening balances are for customers or vendors")
        with self.db.tx():
            jid = self.ledger.post(entry_date or today_str(), "OB", party_id, "OPENING",
                                   memo or f"Opening balance ({party_type})", lines, user_id)
            audit(self.db, user_id, "party_opening", party_type, party_id, {"amount": amount})
        return jid
