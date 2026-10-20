"""Receiving inventory from vendors, purchase orders, vendor bills and payments.

Receiving adds to each item's on-hand in the same transaction that records the voucher, so the Items list
and the register show the new stock the moment the voucher is saved."""
from __future__ import annotations

from datetime import date, timedelta

from .auth import audit
from .db import Database, now_str, today_str
from .inventory import INVENTORY_ACCOUNT, Inventory
from .ledger import Ledger, Line
from .money import round_cents, round_qty
from .units import get_unit


class PurchaseError(Exception):
    pass


class PurchasingService:
    def __init__(self, db: Database, ledger: Ledger, inventory: Inventory):
        self.db = db
        self.ledger = ledger
        self.inv = inventory

    def receive(self, vendor_id: int | None, lines: list[dict], user_id: int, bill_no: str = "",
                bill_date: str | None = None, paid_account: str | None = None, vendor_name: str = "",
                notes: str = "", po_id: int | None = None, update_cost_only: bool = False) -> int:
        """Receive a vendor invoice. lines: [{'item_id', 'qty', 'unit' (any of the item's units; empty = its own
        unit, e.g. 'case'), 'unit_cost' (cents per entered unit)}].
        paid_account None = the bill is owed to the vendor (Accounts payable); else paid now from that account."""
        if not lines:
            raise PurchaseError("Add at least one item")
        vendor = self.db.one("SELECT * FROM vendors WHERE id = ?", (vendor_id,)) if vendor_id else None
        if paid_account is None and vendor is None:
            raise PurchaseError("Choose a vendor, or mark the invoice as paid")
        if paid_account is not None and not self.db.val("SELECT 1 FROM accounts WHERE code = ?", (paid_account,)):
            raise PurchaseError("Unknown payment account")
        prepared = []
        for ln in lines:
            item = self.inv.item(int(ln["item_id"]))
            if item is None or not self.inv.is_stocked(item):
                raise PurchaseError("Only inventory items can be received")
            entered = round_qty(ln["qty"])
            if entered <= 0:
                raise PurchaseError(f"Quantity for {item['name']} must be more than zero")
            cost = float(ln.get("unit_cost") or 0)
            if cost < 0:
                raise PurchaseError(f"Cost for {item['name']} cannot be negative")
            u = get_unit(self.db, item, ln.get("unit") or None)
            base_qty = round_qty(entered * u.factor)
            line_net = round_cents(entered * cost)
            prepared.append((item, entered, u.code, base_qty, line_net, line_net / base_qty))
        subtotal = sum(p[4] for p in prepared)
        total = subtotal
        bill_date = bill_date or today_str()
        terms = int(vendor["terms_days"] or 0) if vendor else 0
        due = (date.fromisoformat(bill_date) + timedelta(days=terms)).isoformat() if terms else bill_date
        with self.db.tx():
            doc_no = f"RV-{self.db.next_seq('RV'):06d}"
            pid = self.db.insert(
                "INSERT INTO purchases(doc_no, kind, vendor_id, vendor_name, bill_no, bill_date, due_date, created_at,"
                " user_id, subtotal, total, paid_amount, payment_account, po_id, notes)"
                " VALUES(?,'receive',?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_no, vendor_id, vendor_name or (vendor["name"] if vendor else ""), bill_no or None, bill_date,
                 due, now_str(), user_id, subtotal, total, total if paid_account else 0, paid_account, po_id,
                 notes or None))
            jl: list[Line] = []
            for item, entered, unit, base_qty, line_net, unit_cost in prepared:
                self.db.exec("INSERT INTO purchase_lines(purchase_id, item_id, purchase_qty, purchase_unit, qty,"
                             " unit_cost, line_net) VALUES(?,?,?,?,?,?,?)",
                             (pid, item["id"], entered, unit, base_qty, unit_cost, line_net))
                value = self.inv.receive(item["id"], base_qty, unit_cost, "receive", "RV", pid, user_id)
                jl.append(Line(INVENTORY_ACCOUNT, debit=value))
                rounding = line_net - value
                if rounding:
                    jl.append(Line("5100", debit=rounding))
                if vendor_id and not item["vendor_id"]:
                    self.db.exec("UPDATE items SET vendor_id = ? WHERE id = ?", (vendor_id, item["id"]))
            if paid_account:
                jl.append(Line(paid_account, credit=total))
            else:
                jl.append(Line("2000", credit=total, party_type="vendor", party_id=vendor_id))
            who = vendor_name or (vendor["name"] if vendor else "vendor")
            memo = f"Received from {who}" + (f", invoice {bill_no}" if bill_no else "")
            self.ledger.post(bill_date, "RV", pid, doc_no, memo, jl, user_id)
            if po_id:
                self.db.exec("UPDATE purchase_orders SET status = 'received' WHERE id = ?", (po_id,))
            audit(self.db, user_id, "receive", "purchase", pid, {"doc_no": doc_no, "total": total, "bill_no": bill_no})
        return pid

    def vendor_return(self, vendor_id: int, lines: list[dict], user_id: int, ref: str = "",
                      return_date: str | None = None, refund_account: str | None = None, notes: str = "") -> int:
        """Return goods to a vendor (QuickBooks POS "return voucher"). lines: [{'item_id', 'qty', 'unit',
        'unit_cost' (the credit per entered unit, normally what was paid)}].

        Stock leaves at its average cost (like any issue). The vendor's credit is: refund_account None = a
        credit on what we owe them (Accounts payable), else money received into that account. A difference
        between the credit and the stock's average cost goes to 5100 Inventory cost corrections."""
        if not lines:
            raise PurchaseError("Add at least one item")
        vendor = self.db.one("SELECT * FROM vendors WHERE id = ?", (vendor_id,))
        if vendor is None:
            raise PurchaseError("Choose the vendor")
        if refund_account is not None and not self.db.val("SELECT 1 FROM accounts WHERE code = ?", (refund_account,)):
            raise PurchaseError("Unknown refund account")
        return_date = return_date or today_str()
        with self.db.tx():
            prepared, needed = [], {}
            for ln in lines:
                item = self.inv.item(int(ln["item_id"]))
                if item is None or not self.inv.is_stocked(item):
                    raise PurchaseError("Only inventory items can be returned to a vendor")
                entered = round_qty(ln["qty"])
                if entered <= 0:
                    raise PurchaseError(f"Quantity for {item['name']} must be more than zero")
                cost = float(ln.get("unit_cost") or 0)
                if cost < 0:
                    raise PurchaseError(f"Credit for {item['name']} cannot be negative")
                u = get_unit(self.db, item, ln.get("unit") or None)
                base_qty = round_qty(entered * u.factor)
                needed[item["id"]] = round_qty(needed.get(item["id"], 0) + base_qty)
                if needed[item["id"]] > float(item["on_hand"]) + 1e-9:
                    raise PurchaseError(f"Only {item['on_hand']:g} {item['unit']} of {item['name']} are on hand; "
                                        f"{needed[item['id']]:g} cannot be returned. Count the item first if the "
                                        "on-hand figure is wrong.")
                credit = round_cents(entered * cost)
                prepared.append((item, entered, u.code, base_qty, credit))
            total = sum(p[4] for p in prepared)
            doc_no = f"VR-{self.db.next_seq('VR'):06d}"
            pid = self.db.insert(
                "INSERT INTO purchases(doc_no, kind, vendor_id, vendor_name, bill_no, bill_date, due_date, created_at,"
                " user_id, subtotal, total, paid_amount, payment_account, notes)"
                " VALUES(?,'vendor_return',?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_no, vendor_id, vendor["name"], ref or None, return_date, return_date, now_str(), user_id, total,
                 total, total if refund_account else 0, refund_account, notes or None))
            stock_value = 0
            for item, entered, unit, base_qty, credit in prepared:
                self.db.exec("INSERT INTO purchase_lines(purchase_id, item_id, purchase_qty, purchase_unit, qty,"
                             " unit_cost, line_net) VALUES(?,?,?,?,?,?,?)",
                             (pid, item["id"], entered, unit, base_qty, credit / base_qty, credit))
                _cost, value = self.inv.issue(item["id"], base_qty, "vendor_return", "VR", pid, user_id,
                                              note=ref or None)
                stock_value += value
            jl: list[Line] = [Line(INVENTORY_ACCOUNT, credit=stock_value)]
            if refund_account:
                jl.append(Line(refund_account, debit=total))
            else:
                jl.append(Line("2000", debit=total, party_type="vendor", party_id=vendor_id))
            diff = total - stock_value
            if diff > 0:
                jl.append(Line("5100", credit=diff))
            elif diff < 0:
                jl.append(Line("5100", debit=-diff))
            memo = f"Returned to {vendor['name']}" + (f", ref {ref}" if ref else "")
            self.ledger.post(return_date, "VR", pid, doc_no, memo, jl, user_id)
            audit(self.db, user_id, "vendor_return", "purchase", pid, {"doc_no": doc_no, "total": total, "ref": ref})
        return pid

    def pay_vendor(self, vendor_id: int, amount: int, account: str, user_id: int, ref: str = "", notes: str = "",
                   pay_date: str | None = None) -> int:
        if amount <= 0:
            raise PurchaseError("Amount must be more than zero")
        if not self.db.val("SELECT 1 FROM vendors WHERE id = ?", (vendor_id,)):
            raise PurchaseError("Vendor not found")
        pay_date = pay_date or today_str()
        with self.db.tx():
            pid = self.db.insert("INSERT INTO vendor_payments(created_at, pay_date, vendor_id, amount, account, ref,"
                                 " user_id, notes) VALUES(?,?,?,?,?,?,?,?)",
                                 (now_str(), pay_date, vendor_id, amount, account, ref or None, user_id, notes or None))
            self.ledger.post(pay_date, "VPAY", pid, f"VP-{pid}", "Vendor payment" + (f" (check {ref})" if ref else ""),
                             [Line("2000", debit=amount, party_type="vendor", party_id=vendor_id),
                              Line(account, credit=amount)], user_id)
            audit(self.db, user_id, "vendor_payment", "vendor", vendor_id, {"amount": amount, "ref": ref})
        return pid

    def vendor_balance(self, vendor_id: int) -> int:
        return self.ledger.party_balance("vendor", vendor_id, "2000")

    def open_bills(self, vendor_id: int | None = None) -> list[dict]:
        """Vendor bills not yet fully paid, oldest first; payments are applied to the oldest bills first."""
        sql = "SELECT * FROM purchases WHERE kind = 'receive' AND payment_account IS NULL"
        params: list = []
        if vendor_id:
            sql += " AND vendor_id = ?"
            params.append(vendor_id)
        bills = self.db.dicts(sql + " ORDER BY vendor_id, COALESCE(due_date, bill_date), id", params)
        paid = {r["vendor_id"]: int(r["a"]) for r in self.db.all(
            "SELECT vendor_id, COALESCE(SUM(amount),0) a FROM vendor_payments GROUP BY vendor_id")}
        # vendor returns taken as a credit on the account count like payments (oldest bill first)
        for r in self.db.all("SELECT vendor_id, COALESCE(SUM(total),0) a FROM purchases WHERE kind = 'vendor_return'"
                             " AND payment_account IS NULL GROUP BY vendor_id"):
            paid[r["vendor_id"]] = paid.get(r["vendor_id"], 0) + int(r["a"])
        out = []
        for b in bills:
            left = paid.get(b["vendor_id"], 0)
            use = min(left, b["total"])
            paid[b["vendor_id"]] = left - use
            b["open"] = b["total"] - use
            if b["open"] > 0:
                out.append(b)
        return out

    # ---- purchase orders ----------------------------------------------------------------------
    def create_po(self, vendor_id: int | None, lines: list[dict], user_id: int | None, notes: str = "",
                  by_agent: bool = False) -> int:
        if not lines:
            raise PurchaseError("Add at least one item")
        with self.db.tx():
            po_no = f"PO-{self.db.next_seq('PO'):05d}"
            po_id = self.db.insert("INSERT INTO purchase_orders(po_no, vendor_id, created_at, user_id, created_by_agent,"
                                   " notes) VALUES(?,?,?,?,?,?)",
                                   (po_no, vendor_id, now_str(), user_id, 1 if by_agent else 0, notes or None))
            for ln in lines:
                self.db.exec("INSERT INTO po_lines(po_id, item_id, qty, est_cost, unit, unit_factor) VALUES(?,?,?,?,?,?)",
                             (po_id, ln["item_id"], ln["qty"], ln.get("est_cost", 0), ln.get("unit") or None,
                              float(ln.get("unit_factor") or 1)))
            audit(self.db, user_id, "po_create", "purchase_order", po_id, {"po_no": po_no, "agent": by_agent})
        return po_id

    def set_po_status(self, po_id: int, status: str, user_id: int) -> None:
        if status not in ("draft", "ordered", "received", "cancelled"):
            raise PurchaseError("Unknown status")
        with self.db.tx():
            self.db.exec("UPDATE purchase_orders SET status = ? WHERE id = ?", (status, po_id))
            audit(self.db, user_id, "po_status", "purchase_order", po_id, {"status": status})
