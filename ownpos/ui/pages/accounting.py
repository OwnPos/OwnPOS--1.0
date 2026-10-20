"""Accounting: account balances, expenses, deposits and transfers (card settlements with fees), sales tax
payments, opening balances and manual journals. Every sale, receiving voucher and payout posts here by itself."""
from __future__ import annotations

from datetime import date

from PySide6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QLineEdit, QVBoxLayout, QWidget

from ...core.db import today_str
from ..dialogs import BaseDialog
from ..widgets.common import (DateEdit, KpiTile, MoneyEdit, RefCombo, button, confirm, error, guarded, hbox, label,
                              page_header, usd)
from ..widgets.grid import Col, DataGrid
from ..widgets.lines import LinesTable


def money_accounts(db, clearing: bool = False) -> list[tuple]:
    """Cash and bank accounts. clearing=True also lists 1030 card sales / 1040 checks to be deposited
    (the place deposits and card settlements come FROM)."""
    rows = db.all("SELECT code, name FROM accounts WHERE active = 1 AND subtype IN ('cash','bank') ORDER BY code")
    return [(r["code"], f"{r['code']} {r['name']}") for r in rows
            if clearing or r["code"] not in ("1030", "1040")]


class ExpenseDialog(BaseDialog):
    def __init__(self, parent, s, win):
        super().__init__(parent, "Record an expense", 480)
        accts = [(r["code"], f"{r['code']} {r['name']}") for r in s.db.all(
            "SELECT code, name FROM accounts WHERE type IN ('expense','cogs') AND active = 1 ORDER BY code")]
        from ..typenew import make_creator
        self.account = RefCombo(accts, on_new=make_creator(self, win, "expense_account"))
        self.date = DateEdit()
        self.payee = QLineEdit()
        self.payee.setPlaceholderText("e.g. NIPSCO, landlord, ADT")
        self.vendor = RefCombo([(v["id"], v["name"]) for v in s.db.all(
            "SELECT id, name FROM vendors WHERE active = 1 ORDER BY name")], blank="(none)",
            on_new=make_creator(self, win, "vendor"))
        self.amount = MoneyEdit(0)
        self.paid = RefCombo(money_accounts(s.db) + [("2000", "On account (owe the vendor)")])
        self.paid.set_value("1100")
        self.ref = QLineEdit()
        self.ref.setPlaceholderText("Check # / bill #")
        self.note = QLineEdit()
        form = QFormLayout()
        for lbl, w in (("Expense account", self.account), ("Date", self.date), ("Paid to", self.payee),
                       ("Vendor (optional)", self.vendor), ("Amount", self.amount), ("Paid from", self.paid),
                       ("Check / ref #", self.ref), ("Note", self.note)):
            form.addRow(lbl, w)
        self.lay.addLayout(form)
        self.lay.addWidget(label("Paying from 1000 Cash in registers needs a drawer open on this station (the payout "
                                 "is then counted at its Z-Out).", "muted", wrap=True))
        self.buttons("Save expense")

    def validate(self):
        if self.amount.cents() <= 0:
            error(self, "Enter the amount")
            return False
        if self.paid.value() == "2000" and not self.vendor.value():
            error(self, "Choose the vendor when the expense is on account")
            return False
        return True


class TransferDialog(BaseDialog):
    def __init__(self, parent, s):
        super().__init__(parent, "Deposit / transfer money", 480)
        self.src = RefCombo(money_accounts(s.db, clearing=True))
        self.src.set_value("1030")
        self.dst = RefCombo(money_accounts(s.db, clearing=True))
        self.dst.set_value("1100")
        self.amount = MoneyEdit(0)
        self.fee = MoneyEdit(0)
        self.date = DateEdit()
        self.memo = QLineEdit("Card settlement")
        form = QFormLayout()
        for lbl, w in (("From", self.src), ("To", self.dst), ("Amount (before fees)", self.amount),
                       ("Fees taken out (card processor / bank)", self.fee), ("Date", self.date), ("Memo", self.memo)):
            form.addRow(lbl, w)
        self.lay.addLayout(form)
        self.lay.addWidget(label("Examples: card settlement 1030 → 1100 with the processing fee; cash deposit "
                                 "1010 Cash in safe → 1100; checks deposited 1040 → 1100. The fee is posted to "
                                 "6400 Card processing fees.", "muted", wrap=True))
        self.buttons("Save")

    def validate(self):
        if self.src.value() == self.dst.value() or self.amount.cents() <= 0:
            error(self, "Choose two different accounts and an amount")
            return False
        if self.fee.cents() < 0 or self.fee.cents() >= self.amount.cents():
            error(self, "The fee must be less than the amount")
            return False
        return True


class SalesTaxPaymentDialog(BaseDialog):
    def __init__(self, parent, s):
        super().__init__(parent, "Pay Indiana sales tax", 520)
        bal = s.ledger.balance("2100")
        self.period = QLineEdit()
        self.period.setPlaceholderText("e.g. Q3 2026 (Jul-Sep)")
        self.amount = MoneyEdit(0)
        self.allowance = MoneyEdit(0)
        self.paid = RefCombo(money_accounts(s.db, clearing=False))
        self.paid.set_value("1100")
        self.date = DateEdit()
        self.ref = QLineEdit()
        self.ref.setPlaceholderText("INTIME confirmation #")
        form = QFormLayout()
        for lbl, w in (("Period of the return", self.period), ("Paid to Indiana DOR", self.amount),
                       ("Collection allowance / rounding kept", self.allowance), ("Paid from", self.paid),
                       ("Payment date", self.date), ("Confirmation #", self.ref)):
            form.addRow(lbl, w)
        self.lay.addWidget(label(f"Sales tax payable now (account 2100): {usd(bal)}", "sectionTitle"))
        self.lay.addLayout(form)
        self.lay.addWidget(label("Enter the figures from the filed ST-103 return. 'Paid' is the amount sent to the "
                                 "state; the allowance line is any part of the collected tax the return lets the store "
                                 "keep (Line 6) or a rounding difference your accountant clears. Both reduce Sales tax "
                                 "payable; the allowance is posted as income (4800).", "muted", wrap=True))
        self.buttons("Record payment")

    def validate(self):
        if self.amount.cents() < 0 or self.allowance.cents() < 0 or self.amount.cents() + self.allowance.cents() <= 0:
            error(self, "Enter the amount paid")
            return False
        return True


class JournalDialog(BaseDialog):
    def __init__(self, parent, s):
        super().__init__(parent, "Manual journal entry", 720)
        accts = s.db.dicts("SELECT code AS id, code AS item_no, name FROM accounts WHERE active = 1 ORDER BY code")
        self.memo = QLineEdit()
        self.date = DateEdit()
        form = QFormLayout()
        form.addRow("Memo *", self.memo)
        form.addRow("Date", self.date)
        self.lay.addLayout(form)
        self.lines = LinesTable([("account", "Account", "item"), ("debit", "Debit", "money"),
                                 ("credit", "Credit", "money")], accts)
        for _ in range(2):
            self.lines.add_row()
        self.lay.addWidget(self.lines, 1)
        self.bal = label("", "sectionTitle")
        self.lay.addLayout(hbox(button("Add line", slot=lambda: self.lines.add_row()), None, self.bal))
        self.lines.changed.connect(self._bal)
        self.lay.addWidget(label("Debits must equal credits. Entries cannot be deleted later, only reversed with "
                                 "another entry. Customer and vendor balances (1200, 2000) are changed through "
                                 "payments and opening balances, not here.", "muted", wrap=True))
        self.buttons("Post entry")
        self.resize(760, 500)

    def _bal(self):
        v = self.lines.values()
        d, c = sum(x["debit"] for x in v), sum(x["credit"] for x in v)
        self.bal.setText(f"Debits {usd(d)} · Credits {usd(c)}" + ("" if d == c else "  (not balanced)"))

    def validate(self):
        v = self.lines.values()
        if not self.memo.text().strip():
            error(self, "Enter a memo")
            return False
        if not v or sum(x["debit"] for x in v) != sum(x["credit"] for x in v) or sum(x["debit"] for x in v) == 0:
            error(self, "Debits and credits must be equal (and not zero)")
            return False
        if any(x["debit"] and x["credit"] for x in v):
            error(self, "Put each line's amount in Debit or Credit, not both")
            return False
        if any(x["account"] in ("1200", "2000", "1300") for x in v):
            error(self, "1200 Accounts receivable, 2000 Accounts payable and 1300 Inventory are kept by the program "
                        "(payments, opening balances, receiving and counts). Use those screens instead.")
            return False
        return True


class AccountingPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.setObjectName("page")
        self.win = win
        self.s = win.s
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        u = win.user
        acts = []
        if u.can("accounting"):
            acts += [button("Record expense", "primary", self.expense),
                     button("Deposit / transfer", slot=self.transfer),
                     button("Pay sales tax", slot=self.pay_tax)]
        if u.role == "owner":
            acts += [button("Manual journal", slot=self.journal), button("Opening balance", slot=self.opening)]
        acts += [button("Profit && Loss", slot=lambda: self._report("profit_loss", "This month")),
                 button("Balance Sheet", slot=lambda: self._report("balance_sheet", "Today")),
                 button("Sales tax (ST-103)", slot=lambda: self._report("sales_tax_liability", "Last quarter"))]
        lay.addLayout(page_header("Accounting", "Sales, receiving, payouts and payments post here by themselves. "
                                  "Statements are in the Report Center.", *acts))
        tiles = QHBoxLayout()
        self.k_income = KpiTile("Net sales this month (before tax)")
        self.k_gp = KpiTile("Gross profit this month")
        self.k_net = KpiTile("Net profit this month")
        self.k_tax = KpiTile("Sales tax payable now")
        self.k_cash = KpiTile("Cash + bank")
        for k in (self.k_income, self.k_gp, self.k_net, self.k_tax, self.k_cash):
            tiles.addWidget(k)
        lay.addLayout(tiles)
        self.grid = DataGrid([Col("code", "Account #", width=80), Col("name", "Account", width=320),
                              Col("type", "Type", width=110), Col("balance", "Balance", "money")],
                             key="coa", export_title="Account balances")
        self.grid.show_totals = False             # balances of different account types are never added up
        self.grid.activated.connect(self._gl)
        lay.addWidget(label("Account balances (double-click for the General Ledger)", "sectionTitle"))
        lay.addWidget(self.grid, 1)

    def on_show(self):
        led = self.s.ledger
        t = date.fromisoformat(today_str())
        pl = led.profit_and_loss(t.replace(day=1).isoformat(), t.isoformat())
        self.k_income.set(usd(pl["total_income"]))
        self.k_gp.set(usd(pl["gross_profit"]))
        self.k_net.set(usd(pl["net_profit"]))
        self.k_tax.set(usd(led.balance("2100")), "Account 2100")
        self.k_cash.set(usd(sum(led.balance(code) for code, _n in money_accounts(self.s.db, clearing=True))))
        rows = []
        for r in self.s.db.dicts("SELECT code, name, type FROM accounts WHERE active = 1 ORDER BY code"):
            r["balance"] = led.balance(r["code"])
            if r["balance"]:
                rows.append(r)
        self.grid.set_rows(rows)

    def _open_report(self, key):
        from ...core.reports import REGISTRY
        self.win.go("reports")
        page = self.win.pages["reports"]
        page.current = REGISTRY[key]
        page._mem_id = None
        page.title.setText(page.current.title)
        page._show_params(page.current.params)
        return page

    def _report(self, key, preset):
        page = self._open_report(key)
        page._preset(preset)
        page.run()

    def _gl(self, row):
        if not self.win.user.can("financial_reports"):
            return
        page = self._open_report("general_ledger")
        page.title.setText(f"General Ledger {row['code']} {row['name']}")
        page.account.set_value(row["code"])
        page._preset("This year")
        page.run()

    @guarded
    def expense(self):
        d = ExpenseDialog(self, self.s, self.win)
        if d.exec() == QDialog.Accepted:
            self.s.accounting.expense(d.account.value(), d.amount.cents(), d.paid.value(), self.win.user.id,
                                      d.date.iso(), d.payee.text().strip(), d.vendor.value(), d.ref.text().strip(),
                                      d.note.text().strip())
            if d.paid.value() == "1000":
                self.win.open_drawer()
            self.on_show()
            self.win.toast("Expense saved.")

    @guarded
    def transfer(self):
        d = TransferDialog(self, self.s)
        if d.exec() == QDialog.Accepted:
            self.s.accounting.transfer(d.src.value(), d.dst.value(), d.amount.cents(), self.win.user.id,
                                       d.fee.cents(), d.memo.text().strip() or "Transfer", d.date.iso())
            self.on_show()
            self.win.toast("Saved.")

    @guarded
    def pay_tax(self):
        d = SalesTaxPaymentDialog(self, self.s)
        if d.exec() == QDialog.Accepted:
            total = d.amount.cents() + d.allowance.cents()
            bal = self.s.ledger.balance("2100")
            if total > bal and not confirm(self, f"{usd(total)} is more than the {usd(bal)} sales tax payable. "
                                                 "Record it anyway?"):
                return
            self.s.accounting.pay_sales_tax(d.amount.cents(), d.paid.value(), self.win.user.id, d.date.iso(),
                                            d.allowance.cents(), d.period.text().strip(), d.ref.text().strip())
            self.on_show()
            self.win.toast("Sales tax payment recorded.")

    @guarded
    def journal(self):
        if self.win.user.role != "owner":
            error(self, "Only the owner can post manual journal entries.")
            return
        d = JournalDialog(self, self.s)
        if d.exec() == QDialog.Accepted:
            lines = [{"account": v["account"], "debit": v["debit"], "credit": v["credit"]} for v in d.lines.values()]
            self.s.accounting.manual_journal(lines, d.memo.text().strip(), self.win.user.id, d.date.iso())
            self.on_show()

    @guarded
    def opening(self):
        if self.win.user.role != "owner":
            error(self, "Only the owner can enter opening balances.")
            return
        d = BaseDialog(self, "Opening balance", 460)
        acct = RefCombo(money_accounts(self.s.db, clearing=True) +
                        [("1500", "1500 Equipment and fixtures"), ("2100", "2100 Sales tax payable"),
                         ("2300", "2300 Loans")])
        amt = MoneyEdit(0)
        when = DateEdit()
        form = QFormLayout()
        form.addRow("Account", acct)
        form.addRow("Balance on the start day", amt)
        form.addRow("Start day", when)
        d.lay.addLayout(form)
        d.lay.addWidget(label("Use once, for the day you start with OwnPOS. Stock on hand is entered in Receive & "
                              "Adjust > Physical count (opening stock mode) or with the Item List import. What each "
                              "customer owes and what you owe each vendor are entered per customer / vendor: their "
                              "list > Import from Excel with an 'Opening balance' column.", "muted", wrap=True))
        d.buttons("Save")
        if d.exec() == QDialog.Accepted and amt.cents():
            if not confirm(self, f"Post an opening balance of {usd(amt.cents())} for {acct.currentText()}?"):
                return
            self.s.accounting.opening_balance(acct.value(), amt.cents(), self.win.user.id, when.iso())
            self.on_show()
