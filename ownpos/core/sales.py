"""Sales receipts, returns and voids, holds, and house-charge accounts.

Receipts: one store-wide receipt number sequence (sales and returns), handed out under the write lock so
four stations can never print the same number. A saved receipt is never edited or deleted (database
triggers); a return or void is its own receipt that points to the original.

Every cart carries a sale key made when the cart starts. The server refuses a second receipt with the
same key, and `finalize` returns the existing receipt if the key was already saved, so a sale whose
"Save" was interrupted by a network drop can be retried safely and is never charged twice."""
from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field

from .auth import audit
from .barcode import lookup_keys, normalize_code
from .db import Database, now_str, today_str
from .inventory import INVENTORY_ACCOUNT, Inventory
from .ledger import Ledger, Line
from .money import round_cents, round_qty
from .tax import calc_receipt
from .units import get_unit, unit_price

RECEIPT_SEQ = "RECEIPT"


class SaleError(Exception):
    pass


@dataclass
class CartLine:
    item_id: int | None
    description: str
    qty: float
    price: int                       # price per unit before tax (cents)
    unit: str = "ea"
    discount: int = 0                # discount for the whole line (cents)
    tax_code: str = "IN7"
    tax_rate: float = 7.0
    income_account: str = "4000"
    department_id: int | None = None
    item_no: str = ""
    upc: str = ""
    list_price: int = 0              # price before any override
    note: str = ""
    factor: float = 1.0              # item units in one of `unit` (a case of 24 = 24)
    base_unit: str = ""
    size: str = ""

    @property
    def base_qty(self) -> float:
        """How much stock this line takes, in the item's own unit."""
        return round_qty(self.qty * (self.factor or 1))


@dataclass
class Cart:
    lines: list[CartLine] = field(default_factory=list)
    customer_id: int | None = None
    customer_name: str = ""
    price_level: int = 1
    tax_exempt: bool = False
    discount_reason: str = ""
    discount_pct: float = 0.0        # a receipt-wide discount (e.g. Employee 10%) that also covers lines added later
    note: str = ""
    sale_key: str = field(default_factory=lambda: uuid.uuid4().hex)
    hours_override: str = ""         # set when the cashier continued past the sale-hours warning

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, text: str) -> "Cart":
        data = json.loads(text)
        lines = [CartLine(**ln) for ln in data.pop("lines", [])]
        known = {f for f in cls.__dataclass_fields__}
        return cls(lines=lines, **{k: v for k, v in data.items() if k in known})


class SalesService:
    def __init__(self, db: Database, ledger: Ledger, inventory: Inventory):
        self.db = db
        self.ledger = ledger
        self.inv = inventory

    # ---- finding items ------------------------------------------------------------------------
    def find_item(self, code: str):
        """(item row, unit code) for a scanned or typed code, None if nothing matches, or raises
        SaleError listing the items when one code fits several of them.

        Order: exact UPC -> exact pack/case barcode -> UPC with leading zeros or check digit differing ->
        alternate lookup -> item number -> exact name. Each step only accepts a single match."""
        code = normalize_code(code)
        if not code:
            return None
        db = self.db
        rows = db.all("SELECT * FROM items WHERE active = 1 AND upc = ? LIMIT 2", (code,))
        if len(rows) == 1:
            return rows[0], None
        if len(rows) > 1:
            raise SaleError(self._many(rows, code))
        u = db.one("SELECT unit, item_id FROM item_units u WHERE barcode = ? AND EXISTS"
                   " (SELECT 1 FROM items i WHERE i.id = u.item_id AND i.active = 1) LIMIT 1", (code,))
        if u:
            return self.inv.item(u["item_id"]), u["unit"]
        keys = lookup_keys(code)
        if keys:
            # the full code (without leading zeros) first, then the check-digit variants
            for key in keys:
                rows = db.all("SELECT * FROM items WHERE active = 1 AND upc_key = ? LIMIT 2", (key,))
                units = db.all("SELECT u.unit, u.item_id FROM item_units u JOIN items i ON i.id = u.item_id"
                               " WHERE i.active = 1 AND u.barcode_key = ? LIMIT 2", (key,))
                found = [(r, None) for r in rows] + [(self.inv.item(x["item_id"]), x["unit"]) for x in units]
                if len(found) == 1:
                    return found[0]
                if len(found) > 1:
                    raise SaleError(self._many([f[0] for f in found], code))
        for sql in ("SELECT * FROM items WHERE active = 1 AND lower(alt_lookup) = lower(?) LIMIT 2",
                    "SELECT * FROM items WHERE active = 1 AND lower(item_no) = lower(?) LIMIT 2",
                    "SELECT * FROM items WHERE active = 1 AND lower(name) = lower(?) LIMIT 2"):
            rows = db.all(sql, (code,))
            if len(rows) == 1:
                return rows[0], None
            if len(rows) > 1:
                raise SaleError(self._many(rows, code))
        return None

    @staticmethod
    def _many(rows, code) -> str:
        names = ", ".join(f"{r['name']} (#{r['item_no']})" for r in rows[:5])
        return f"The code {code} belongs to more than one item: {names}. Fix the codes in Items, then scan again."

    def search_items(self, text: str, limit: int = 40) -> list:
        """Items whose name, size, item number, UPC or alternate lookup contains every word typed."""
        words = [w for w in text.strip().split() if w]
        if not words:
            return []
        cond, params = [], []
        for w in words:
            like = f"%{w}%"
            cond.append("(name ILIKE ? OR COALESCE(size,'') ILIKE ? OR item_no ILIKE ? OR COALESCE(upc,'') LIKE ?"
                        " OR COALESCE(alt_lookup,'') ILIKE ?)")
            params += [like, like, like, like, like]
        params.append(limit)
        return self.db.all("SELECT * FROM items WHERE active = 1 AND " + " AND ".join(cond) +
                           " ORDER BY on_register DESC, name LIMIT ?", params)

    # ---- building a cart ----------------------------------------------------------------------
    def make_line(self, item, qty: float = 1, price_level: int = 1, unit: str | None = None) -> CartLine:
        tax = self.db.one("SELECT * FROM tax_codes WHERE code = ?", (item["tax_code"],))
        dept = self.db.one("SELECT income_account FROM departments WHERE id = ?", (item["department_id"],))
        u = get_unit(self.db, item, unit)
        price = unit_price(item, u, price_level)
        desc = item["name"] if u.base else f"{item['name']} {u.code}"
        return CartLine(
            item_id=item["id"], description=desc, qty=round_qty(qty), price=price, unit=u.code,
            tax_code=item["tax_code"], tax_rate=float(tax["rate"]) if tax else 0.0,
            income_account=dept["income_account"] if dept else "4000", department_id=item["department_id"],
            item_no=item["item_no"], upc=item["upc"] or "", list_price=price, factor=u.factor,
            base_unit=item["unit"], size=item["size"] or "")

    def set_line_unit(self, ln: CartLine, unit: str, price_level: int = 1) -> None:
        """Sell the line in another unit of the same item (can -> 6pk -> case). The price becomes that unit's
        price; a discount on the line is cleared because it was for the old unit."""
        item = self.inv.item(ln.item_id) if ln.item_id else None
        if item is None:
            raise SaleError("This line has no item")
        u = get_unit(self.db, item, unit)
        ln.unit, ln.factor, ln.base_unit = u.code, u.factor, item["unit"]
        ln.description = item["name"] if u.base else f"{item['name']} {u.code}"
        ln.price = ln.list_price = unit_price(item, u, price_level)
        ln.discount = 0

    def reprice(self, cart: Cart) -> None:
        """Apply the cart's price level to every line that still has its list price (after choosing a customer)."""
        for ln in cart.lines:
            if not ln.item_id or ln.price != ln.list_price:
                continue
            item = self.inv.item(ln.item_id)
            if item is None:
                continue
            u = get_unit(self.db, item, ln.unit)
            ln.price = ln.list_price = unit_price(item, u, cart.price_level)

    def totals(self, cart: Cart) -> dict:
        amounts, t = calc_receipt([(ln.qty, ln.price, ln.discount, ln.tax_rate) for ln in cart.lines],
                                  tax_exempt=cart.tax_exempt)
        t["lines"] = amounts
        t["items"] = sum(ln.qty for ln in cart.lines)
        t["savings"] = sum(max(round_cents(ln.qty * ln.list_price) - (a.net), 0) for ln, a in zip(cart.lines, amounts))
        return t

    def apply_sale_discount(self, cart: Cart, pct: float, reason: str = "") -> None:
        """Spread a percentage discount over every line (replaces line discounts), e.g. Employee 10%."""
        if pct < 0 or pct > 100:
            raise SaleError("A discount is between 0 and 100%")
        for ln in cart.lines:
            ln.discount = round_cents(round_cents(ln.qty * ln.price) * pct / 100)
        cart.discount_reason = reason if pct else ""
        cart.discount_pct = float(pct)

    # ---- saving a sale ------------------------------------------------------------------------
    def find_by_key(self, sale_key: str):
        return self.db.one("SELECT * FROM sales WHERE sale_key = ?", (sale_key,))

    def finalize(self, cart: Cart, tenders: list[dict], user_id: int, shift_id: int | None,
                 station_id: int | None = None, sale_date: str | None = None) -> int:
        """tenders: [{'method': 'CASH', 'tendered': cents, 'ref': '', 'auth': ''}, ...]. Returns the receipt id.
        If this cart's sale key was already saved (a retry after a network drop), returns that receipt."""
        done = self.find_by_key(cart.sale_key)
        if done is not None:
            return done["id"]
        if not cart.lines:
            raise SaleError("The sale has no items")
        for ln in cart.lines:
            if ln.qty <= 0:
                raise SaleError(f"Quantity for {ln.description} must be more than zero")
            if ln.price < 0 or ln.discount < 0:
                raise SaleError(f"Price or discount for {ln.description} is negative")
            if ln.discount > round_cents(ln.qty * ln.price):
                raise SaleError(f"Discount on {ln.description} is more than the line amount")
        tot = self.totals(cart)
        total = tot["total"]
        methods = {r["code"]: r for r in self.db.all("SELECT * FROM payment_methods")}
        clean, tendered_sum, cash_tendered = [], 0, 0
        for t in tenders:
            m = methods.get(t["method"])
            if m is None or not m["active"]:
                raise SaleError(f"Unknown payment method {t['method']}")
            amt = int(t["tendered"])
            if amt <= 0:
                continue
            if m["requires_customer"] and not cart.customer_id:
                raise SaleError(f"{m['name']} needs a customer on the sale")
            if m["requires_ref"] and not (t.get("ref") or "").strip():
                raise SaleError(f"Enter the {m['name'].lower()} number")
            tendered_sum += amt
            if m["is_cash"]:
                cash_tendered += amt
            clean.append({"method": m["code"], "tendered": amt, "ref": (t.get("ref") or "").strip(),
                          "auth": (t.get("auth") or "").strip(), "m": m})
        if tendered_sum < total:
            raise SaleError("Payments do not cover the total")
        change = tendered_sum - total
        if change > cash_tendered:
            raise SaleError("Change can only be given from cash")
        sale_date = sale_date or today_str()
        station_id = station_id if station_id is not None else self.db.station_id

        with self.db.tx():
            done = self.find_by_key(cart.sale_key)          # checked again under the write lock
            if done is not None:
                return done["id"]
            if cart.customer_id:
                self._check_credit(cart.customer_id, sum(c["tendered"] for c in clean if c["method"] == "CHARGE"))
                self._check_store_credit(cart.customer_id, sum(c["tendered"] for c in clean
                                                               if c["method"] == "STORECREDIT"))
            seq = self.db.next_seq(RECEIPT_SEQ)
            receipt_no = str(seq)
            costs = []
            strict = not self.db.get_setting("allow_negative_stock", True)
            needed: dict = {}
            for ln in cart.lines:
                item = self.inv.item(ln.item_id) if ln.item_id else None
                if strict and item is not None and self.inv.is_stocked(item):
                    # read under the write lock: another station's sale a second ago is already counted
                    needed[item["id"]] = needed.get(item["id"], 0) + ln.base_qty
                    if needed[item["id"]] > float(item["on_hand"]) + 1e-9:
                        raise SaleError(f"Only {float(item['on_hand']):g} {item['unit']} of {item['name']} on "
                                        "hand. Selling more than on hand is switched off in Settings.")
                if item is not None and self.inv.is_stocked(item):
                    cu = float(item["cost_avg"])
                    costs.append((cu, round_cents(ln.base_qty * cu), item))
                else:
                    costs.append((0.0, 0, item))
            cost_total = sum(c[1] for c in costs)
            customer = self.db.one("SELECT * FROM customers WHERE id = ?", (cart.customer_id,)) if cart.customer_id else None
            sale_id = self.db.insert(
                "INSERT INTO sales(doc_type, receipt_no, seq, sale_key, sale_date, created_at, user_id, station_id,"
                " shift_id, customer_id, customer_name, price_level, discount_reason, subtotal, discount, taxable,"
                " nontaxable, tax, total, cost_total, notes)"
                " VALUES('INV',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (receipt_no, seq, cart.sale_key, sale_date, now_str(), user_id, station_id, shift_id,
                 cart.customer_id, cart.customer_name or (customer["name"] if customer else None),
                 cart.price_level, cart.discount_reason or None, tot["subtotal"], tot["discount"], tot["taxable"],
                 tot["nontaxable"], tot["tax"], total, cost_total, cart.note or None))
            jl: list[Line] = []
            for i, (ln, la, (cu, cv, item)) in enumerate(zip(cart.lines, tot["lines"], costs), start=1):
                self.db.exec(
                    "INSERT INTO sale_lines(sale_id, line_no, item_id, department_id, description, qty, unit,"
                    " unit_factor, price, list_price, discount, net, tax, total, tax_code, tax_rate, income_account,"
                    " cost_unit, cost_total) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (sale_id, i, ln.item_id, ln.department_id, ln.description, ln.qty, ln.unit, ln.factor or 1,
                     ln.price, ln.list_price, la.discount, la.net, la.tax, la.total, ln.tax_code,
                     ln.tax_rate if la.taxable else 0, ln.income_account, cu, cv))
                jl.append(Line(ln.income_account, credit=la.net))
                if item is not None and self.inv.is_stocked(item):
                    self.inv.issue(item["id"], ln.base_qty, "sale", "INV", sale_id, user_id)
                    if cv:
                        jl.append(Line("5000", debit=cv))
                        jl.append(Line(INVENTORY_ACCOUNT, credit=cv))
            jl.append(Line("2100", credit=tot["tax"]))
            remaining_change = change
            for c in clean:
                give = 0
                if c["m"]["is_cash"] and remaining_change:
                    give = min(remaining_change, c["tendered"])
                    remaining_change -= give
                kept = c["tendered"] - give
                self.db.exec("INSERT INTO sale_tenders(sale_id, method_code, amount, tendered, change, ref, auth_code)"
                             " VALUES(?,?,?,?,?,?,?)",
                             (sale_id, c["method"], kept, c["tendered"], give, c["ref"] or None, c["auth"] or None))
                party = ("customer", cart.customer_id) if c["method"] in ("CHARGE", "STORECREDIT") else (None, None)
                jl.append(Line(c["m"]["account"], debit=kept, party_type=party[0], party_id=party[1]))
            self.ledger.post(sale_date, "INV", sale_id, receipt_no, f"Sale #{receipt_no}", jl, user_id, shift_id=shift_id)
            details = {"receipt_no": receipt_no, "total": total, "lines": len(cart.lines)}
            if cart.discount_reason:
                details["discount"] = {"reason": cart.discount_reason, "amount": tot["discount"]}
            if cart.hours_override:
                details["hours_override"] = cart.hours_override
            audit(self.db, user_id, "sale", "sale", sale_id, details)
        return sale_id

    def _check_credit(self, customer_id: int, new_charge: int) -> None:
        if not new_charge:
            return
        limit = int(self.db.val("SELECT credit_limit FROM customers WHERE id = ?", (customer_id,), 0))
        if limit and self.customer_balance(customer_id) + new_charge > limit:
            raise SaleError("This sale would take the customer over their house charge limit")

    def _check_store_credit(self, customer_id: int, used: int) -> None:
        if used and used > self.store_credit(customer_id):
            raise SaleError("The customer does not have that much store credit")

    # ---- printing -----------------------------------------------------------------------------
    def record_print(self, sale_id: int, user_id: int) -> int:
        """Returns the copy number: 0 for the original, 1+ for a reprint."""
        with self.db.tx():
            count = int(self.db.val("SELECT print_count FROM sales WHERE id = ?", (sale_id,), 0))
            self.db.exec("UPDATE sales SET print_count = print_count + 1,"
                         " printed_at = COALESCE(printed_at, ?), printed_by = COALESCE(printed_by, ?) WHERE id = ?",
                         (now_str(), user_id, sale_id))
            self.db.exec("INSERT INTO print_log(sale_id, ts, user_id, copy_no) VALUES(?,?,?,?)",
                         (sale_id, now_str(), user_id, count))
            if count:
                audit(self.db, user_id, "reprint", "sale", sale_id, {"copy_no": count})
        return count

    # ---- returns and voids --------------------------------------------------------------------
    def returnable(self, sale_id: int) -> list[dict]:
        rows = self.db.dicts("SELECT * FROM sale_lines WHERE sale_id = ? ORDER BY line_no", (sale_id,))
        done = {r["ref_line_id"]: float(r["q"]) for r in self.db.all(
            "SELECT l.ref_line_id, COALESCE(SUM(l.qty),0) q FROM sale_lines l JOIN sales s ON s.id = l.sale_id"
            " WHERE s.doc_type = 'CN' AND s.ref_sale_id = ? GROUP BY l.ref_line_id", (sale_id,))}
        for r in rows:
            r["returned"] = done.get(r["id"], 0.0)
            r["returnable"] = round_qty(r["qty"] - r["returned"])
        return rows

    def find_sale(self, receipt_no: str):
        return self.db.one("SELECT * FROM sales WHERE receipt_no = ?", (receipt_no.strip().lstrip("#"),))

    def credit_note(self, sale_id: int, lines: list[dict], refund_method: str | None, user_id: int,
                    shift_id: int | None, reason: str, cancel: bool = False, ref: str = "",
                    sale_key: str | None = None, station_id: int | None = None) -> int:
        """Return goods from a receipt. lines: [{'line_id': id, 'qty': q, 'restock': True}, ...]

        Amounts: each returned line uses cumulative rounding - after this return the total returned for an
        original line is round(original amount x qty returned so far / qty), so the return that takes the last
        remaining quantity gets exactly what is left and all returns of a line add up to the original line
        (net, discount, tax, total, cost).

        Refund: a return is refunded in refund_method. A void (cancel=True) reverses the receipt's own payments,
        each in its own method and amount, net of earlier returns on it."""
        sale_key = sale_key or uuid.uuid4().hex
        done_doc = self.find_by_key(sale_key)
        if done_doc is not None:
            return done_doc["id"]
        orig = self.db.one("SELECT * FROM sales WHERE id = ?", (sale_id,))
        if orig is None or orig["doc_type"] != "INV":
            raise SaleError("Original receipt not found")
        if not orig["is_active"]:
            raise SaleError("This receipt has already been voided")
        if not (reason or "").strip():
            raise SaleError("A reason is required")
        methods = {r["code"]: r for r in self.db.all("SELECT * FROM payment_methods")}
        if cancel:
            fb = methods.get(refund_method) if refund_method else None
            if fb is None or (fb["requires_customer"] and not orig["customer_id"]):
                refund_method = "CASH"
        method = methods.get(refund_method) if refund_method else None
        if method is None:
            raise SaleError("Choose how the refund is paid")
        if method["requires_customer"] and not orig["customer_id"]:
            raise SaleError(f"{method['name']} needs a customer on the original receipt")
        station_id = station_id if station_id is not None else self.db.station_id
        keys = ("discount", "tax", "total", "cost_total")
        with self.db.tx():
            if self.find_by_key(sale_key) is not None:
                return self.find_by_key(sale_key)["id"]
            orig = self.db.one("SELECT * FROM sales WHERE id = ?", (sale_id,))
            if not orig["is_active"]:
                raise SaleError("This receipt has already been voided")
            avail = {r["id"]: r for r in self.returnable(sale_id)}
            picked = []
            for req in lines:
                r = avail.get(req["line_id"])
                q = round_qty(req.get("qty", 0))
                if r is None or q <= 0:
                    continue
                if q > r["returnable"] + 1e-9:
                    raise SaleError(f"Only {r['returnable']:g} of {r['description']} can be returned")
                picked.append((r, q, bool(req.get("restock", True))))
            if not picked:
                raise SaleError("Nothing selected to return")
            sale_date = today_str()
            seq = self.db.next_seq(RECEIPT_SEQ)
            cn_no = str(seq)
            parts = []
            for r, q, restock in picked:
                prev = self.db.one(
                    "SELECT COALESCE(SUM(l.qty),0) qty, COALESCE(SUM(l.discount),0) discount,"
                    " COALESCE(SUM(l.tax),0) tax, COALESCE(SUM(l.total),0) total,"
                    " COALESCE(SUM(l.cost_total),0) cost_total FROM sale_lines l JOIN sales s ON s.id = l.sale_id"
                    " WHERE s.doc_type = 'CN' AND l.ref_line_id = ?", (r["id"],))
                done = round_qty(float(prev["qty"]) + q)
                last = done >= float(r["qty"]) - 1e-9
                p = {}
                for k in keys:
                    target = int(r[k]) if last else round_cents(r[k] * done / float(r["qty"]))
                    p[k] = target - int(prev[k])
                p["net"] = p["total"] - p["tax"]
                gross_line = p["net"] + p["discount"]
                parts.append((r, q, restock, p, gross_line))
            sub = sum(x[4] for x in parts)
            disc = sum(x[3]["discount"] for x in parts)
            tax = sum(x[3]["tax"] for x in parts)
            total = sum(x[3]["total"] for x in parts)
            cost = sum(x[3]["cost_total"] for x in parts)
            taxable = sum(x[3]["net"] for x in parts if x[0]["tax_rate"])
            nontaxable = sum(x[3]["net"] for x in parts if not x[0]["tax_rate"])
            cn_id = self.db.insert(
                "INSERT INTO sales(doc_type, receipt_no, seq, sale_key, sale_date, created_at, user_id, station_id,"
                " shift_id, customer_id, customer_name, price_level, ref_sale_id, reason, subtotal, discount, taxable,"
                " nontaxable, tax, total, cost_total, notes) VALUES('CN',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (cn_no, seq, sale_key, sale_date, now_str(), user_id, station_id, shift_id, orig["customer_id"],
                 orig["customer_name"], orig["price_level"], sale_id, reason.strip(), sub, disc, taxable,
                 nontaxable, tax, total, cost, "Void" if cancel else None))
            jl: list[Line] = []
            for i, (r, q, restock, p, gross_line) in enumerate(parts, start=1):
                self.db.exec(
                    "INSERT INTO sale_lines(sale_id, line_no, item_id, department_id, description, qty, unit,"
                    " unit_factor, price, list_price, discount, net, tax, total, tax_code, tax_rate, income_account,"
                    " cost_unit, cost_total, restock, ref_line_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (cn_id, i, r["item_id"], r["department_id"], r["description"], q, r["unit"],
                     r["unit_factor"] or 1, r["price"], r["list_price"], p["discount"], p["net"], p["tax"],
                     p["total"], r["tax_code"], r["tax_rate"], r["income_account"], r["cost_unit"],
                     p["cost_total"], 1 if restock else 0, r["id"]))
                jl.append(Line("4900", debit=p["net"]))
                item = self.inv.item(r["item_id"]) if r["item_id"] else None
                if item is not None and self.inv.is_stocked(item):
                    if restock:
                        value = self.inv.return_to_stock(item["id"], round_qty(q * (r["unit_factor"] or 1)),
                                                         r["cost_unit"], "CN", cn_id, user_id, value=p["cost_total"])
                        jl.append(Line(INVENTORY_ACCOUNT, debit=value))
                    else:
                        jl.append(Line("5200", debit=p["cost_total"]))
                    jl.append(Line("5000", credit=p["cost_total"]))
            jl.append(Line("2100", debit=tax))
            refunds = self._mirror_tenders(sale_id, total, refund_method) if cancel else [(refund_method, total)]
            for code, amt in refunds:
                m = methods[code]
                party = ("customer", orig["customer_id"]) if code in ("CHARGE", "STORECREDIT") else (None, None)
                jl.append(Line(m["account"], credit=amt, party_type=party[0], party_id=party[1]))
                self.db.exec("INSERT INTO sale_tenders(sale_id, method_code, amount, tendered, change, ref)"
                             " VALUES(?,?,?,?,0,?)", (cn_id, code, amt, amt, ref or None))
            self.ledger.post(sale_date, "CN", cn_id, cn_no, f"Return #{cn_no} for receipt #{orig['receipt_no']}", jl,
                             user_id, shift_id=shift_id)
            if cancel:
                self.db.exec("UPDATE sales SET is_active = 0 WHERE id = ?", (sale_id,))
            audit(self.db, user_id, "void_receipt" if cancel else "return", "sale", cn_id,
                  {"return_no": cn_no, "original": orig["receipt_no"], "total": total, "reason": reason,
                   "refunds": {c: a for c, a in refunds}})
        return cn_id

    # order in which a void hands money back when earlier returns already refunded part of the receipt
    _MIRROR_ORDER = {"CHARGE": 0, "STORECREDIT": 1, "CARD": 2, "CHECK": 3, "CASH": 4}

    def _mirror_tenders(self, sale_id: int, total: int, fallback: str) -> list[tuple[str, int]]:
        orig = {r["method_code"]: int(r["a"]) for r in self.db.all(
            "SELECT method_code, SUM(amount) a FROM sale_tenders WHERE sale_id = ? GROUP BY method_code", (sale_id,))}
        done = {r["method_code"]: int(r["a"]) for r in self.db.all(
            "SELECT t.method_code, SUM(t.amount) a FROM sale_tenders t JOIN sales s ON s.id = t.sale_id"
            " WHERE s.doc_type = 'CN' AND s.ref_sale_id = ? GROUP BY t.method_code", (sale_id,))}
        out: dict[str, int] = {}
        left = total
        for code in sorted(orig, key=lambda c: (self._MIRROR_ORDER.get(c, 2), c)):
            if left <= 0:
                break
            give = min(left, max(orig[code] - done.get(code, 0), 0))
            if give <= 0:
                continue
            out[code] = out.get(code, 0) + give
            left -= give
        if left > 0:
            out[fallback] = out.get(fallback, 0) + left
        return [(c, a) for c, a in out.items() if a]

    def cancel_bill(self, sale_id: int, reason: str, user_id: int, shift_id: int | None,
                    refund_method: str | None = None, sale_key: str | None = None) -> int:
        """Void the rest of a receipt; the refund mirrors the receipt's own payments."""
        lines = [{"line_id": r["id"], "qty": r["returnable"], "restock": True}
                 for r in self.returnable(sale_id) if r["returnable"] > 0]
        if not lines:
            raise SaleError("Everything on this receipt has already been returned")
        return self.credit_note(sale_id, lines, refund_method, user_id, shift_id, reason, cancel=True,
                                sale_key=sale_key)

    void_receipt = cancel_bill

    # ---- holds --------------------------------------------------------------------------------
    def hold(self, cart: Cart, user_id: int, label: str = "") -> int:
        return self.db.insert("INSERT INTO holds(created_at, user_id, station_id, label, data_json) VALUES(?,?,?,?,?)",
                              (now_str(), user_id, self.db.station_id, label or None, cart.to_json()))

    def holds(self) -> list:
        return self.db.all("SELECT h.*, u.username FROM holds h LEFT JOIN users u ON u.id = h.user_id ORDER BY h.id")

    def recall(self, hold_id: int) -> Cart:
        with self.db.tx():
            row = self.db.one("SELECT data_json FROM holds WHERE id = ?", (hold_id,))
            if row is None:
                raise SaleError("That held sale was already recalled (maybe on another station)")
            self.db.exec("DELETE FROM holds WHERE id = ?", (hold_id,))
        return Cart.from_json(row["data_json"])

    # ---- customer accounts --------------------------------------------------------------------
    def customer_balance(self, customer_id: int) -> int:
        """Amount the customer owes on house charge."""
        return self.ledger.party_balance("customer", customer_id, "1200")

    def store_credit(self, customer_id: int) -> int:
        return self.ledger.party_balance("customer", customer_id, "2210")

    def receive_payment(self, customer_id: int, amount: int, method_code: str, user_id: int,
                        shift_id: int | None, ref: str = "", notes: str = "") -> int:
        if amount <= 0:
            raise SaleError("Payment must be more than zero")
        m = self.db.one("SELECT * FROM payment_methods WHERE code = ?", (method_code,))
        if m is None or method_code in ("CHARGE", "STORECREDIT"):
            raise SaleError("Choose cash, card or check for a payment on account")
        with self.db.tx():
            pid = self.db.insert("INSERT INTO customer_payments(created_at, customer_id, amount, method_code, ref,"
                                 " user_id, station_id, shift_id, notes) VALUES(?,?,?,?,?,?,?,?,?)",
                                 (now_str(), customer_id, amount, method_code, ref or None, user_id,
                                  self.db.station_id, shift_id, notes or None))
            self.ledger.post(today_str(), "PAY", pid, f"PMT-{pid}", "Payment on house charge account",
                             [Line(m["account"], debit=amount),
                              Line("1200", credit=amount, party_type="customer", party_id=customer_id)], user_id,
                             shift_id=shift_id)
            audit(self.db, user_id, "customer_payment", "customer", customer_id, {"amount": amount, "method": method_code})
        return pid
