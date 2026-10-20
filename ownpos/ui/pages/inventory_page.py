"""Inventory > Receive & Adjust: receive vendor invoices (scan or pick items), purchase orders, breakage and
waste, physical counts and opening stock, vendor bills and payments.

Every voucher saved here changes the items' ONE on-hand count in the same transaction that records it, so the
Item List and every register see the new stock as soon as it is saved."""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFormLayout, QLineEdit, QTabWidget, QVBoxLayout,
                               QWidget)

from ...core.money import fmt_money, fmt_qty
from ..dialogs import BaseDialog, PickDialog
from ..widgets.common import (DateEdit, MoneyEdit, RefCombo, button, confirm, error, guarded, hbox, info, label,
                              page_header, usd)
from ..widgets.grid import Col, DataGrid
from ..widgets.lines import LinesTable, add_to_model, item_model

COST_JUMP = 0.30        # warn when a received cost differs from the last cost by more than 30 %


def money_accounts(db, drawer: bool = True) -> list[tuple]:
    """Cash and bank accounts a vendor can be paid from. Card and check clearing accounts are not payment sources."""
    rows = db.all("SELECT code, name FROM accounts WHERE active = 1 AND subtype IN ('cash','bank')"
                  " AND code NOT IN ('1030','1040') ORDER BY code")
    return [(r["code"], f"Paid from {r['code']} {r['name']}") for r in rows if drawer or r["code"] != "1000"]


ITEM_SQL = ("SELECT id, item_no, name, size, upc, unit, last_cost, cost_avg, purchase_unit, purchase_factor, on_hand,"
            " vendor_id, department_id FROM items WHERE active = 1 AND kind = 'inventory' ORDER BY name")


class InventoryPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.setObjectName("page")
        self.win = win
        self.s = win.s
        self._stamp = None
        self.items = self.s.db.dicts(ITEM_SQL)
        self.by_id = {it["id"]: it for it in self.items}
        self.model = item_model(self.items)          # one item list shared by every table on this screen
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.addLayout(page_header("Receive & Adjust", "Receive vendor invoices, record breakage, count stock and pay "
                                  "vendors. On-hand changes the moment a voucher is saved."))
        self.tabs = QTabWidget()
        u = win.user
        self._tab_keys = []
        for key, perm, title, make in (("receive", "receive", "Receive inventory", self._receive_tab),
                                       ("vreturn", "receive", "Return to vendor", self._vreturn_tab),
                                       ("po", "purchase_orders", "Purchase orders", self._po_tab),
                                       ("waste", "waste", "Breakage & waste", self._waste_tab),
                                       ("count", "adjust", "Physical count / opening stock", self._count_tab),
                                       ("pay", "vendor_payments", "Vendor bills & payments", self._pay_tab)):
            if u.can(perm):
                self.tabs.addTab(make(), title.replace("&", "&&"))     # one & would be a keyboard shortcut
                self._tab_keys.append(key)
        if not self._tab_keys:
            self.tabs.addTab(label("Your role has no inventory permissions.", "muted"), "Receive && Adjust")
        lay.addWidget(self.tabs, 1)
        self.reload()
        self._fresh = True              # just loaded: the first on_show need not load it all again

    # ---- shared ------------------------------------------------------
    def _units(self, item):
        from ...core.units import units_for
        return units_for(self.s.db, item)

    def _lines(self, cols, on_item=None, on_unit=None) -> LinesTable:
        return LinesTable(cols, self.items, on_item, self._units, on_unit, model=self.model, by_id=self.by_id)

    def _refresh_items(self):
        """Pick up items added or changed on any station since this screen was built."""
        stamp = tuple(self.s.db.one("SELECT COUNT(*), MAX(COALESCE(updated_at, created_at)) FROM items"))
        if stamp == self._stamp:
            return
        self._stamp = stamp
        fresh = self.s.db.dicts(ITEM_SQL)
        add_to_model(self.model, self.by_id, fresh)

    def _scan_to(self, table: LinesTable, box: QLineEdit, qty_key: str = "qty", on_new=None):
        """Scan or type a UPC / item # into box: the item is added to the table, or its quantity goes up by one."""
        code = box.text().strip()
        box.clear()
        if not code:
            return
        try:
            found = self.s.sales.find_item(code)
        except Exception as exc:  # noqa: BLE001 - one code fits several items
            error(self, str(exc))
            return
        if found is None:
            error(self, f"No item has the code {code}. Add it in Item List first (or check the code).")
            return
        item, unit = found
        if item["kind"] != "inventory":
            error(self, f"{item['name']} does not keep stock (item type {item['kind']}).")
            return
        if item["id"] not in self.by_id:
            add_to_model(self.model, self.by_id, self.s.db.dicts(ITEM_SQL.replace("ORDER BY name", "AND id = ?"),
                                                                  (item["id"],)))
        unit = unit or item["unit"]          # the scanned code decides the unit: bottle UPC = one bottle
        r = table.find_row(item["id"], unit)
        if r < 0:
            # reuse an empty row before adding one
            r = next((i for i in range(table.rowCount()) if not table.cellWidget(i, 0).value()), -1)
            if r < 0:
                r = table.add_row()
            table.set_cell(r, table.cols[0][0], item["id"])     # fills units and the cost through on_item
            if any(c[0] == "unit" for c in table.cols):
                table.set_cell(r, "unit", unit)
            table.set_cell(r, qty_key, 1)
        else:
            ci = next(i for i, c in enumerate(table.cols) if c[0] == qty_key)
            now = table.cellWidget(r, ci).value()
            table.set_cell(r, qty_key, round(now + 1, 3))
        table.selectRow(r)
        table.changed.emit()

    # ---- receive -----------------------------------------------------
    def _receive_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        from ..typenew import make_creator
        self.r_vendor = RefCombo([], on_new=make_creator(self, self.win, "vendor"))
        self.r_bill = QLineEdit()
        self.r_bill.setPlaceholderText("The vendor's invoice number")
        self.r_date = DateEdit()
        self.r_paid = RefCombo([])
        self.r_po = None
        self.r_po_label = label("", "muted")
        form = QFormLayout()
        form.addRow("Vendor *", self.r_vendor)
        form.addRow("Vendor invoice #", self.r_bill)
        form.addRow("Invoice date", self.r_date)
        form.addRow("Payment", self.r_paid)
        lay.addLayout(hbox(form, None, vbox_buttons(button("Load a purchase order", slot=self.load_po),
                                                    self.r_po_label)))
        self.r_scan = QLineEdit()
        self.r_scan.setPlaceholderText("Scan a bottle or case, or type a UPC / item #, then Enter: adds the item or "
                                       "adds one more")
        self.r_scan.returnPressed.connect(lambda: self._scan_to(self.r_lines, self.r_scan))
        lay.addWidget(self.r_scan)
        self.r_lines = self._lines([("item_id", "Item", "item"), ("unit", "Unit", "unit"), ("qty", "Qty", "qty"),
                                    ("unit_cost", "Cost per unit ($)", "money"), ("line", "Line total", "readonly"),
                                    ("info", "On hand now", "readonly")], self._r_item, self._r_unit)
        lay.addWidget(self.r_lines, 1)
        self.r_total = label("", "sectionTitle")
        lay.addLayout(hbox(button("Add line", slot=lambda: self.r_lines.add_row()),
                           button("Remove line", slot=self.r_lines.remove_selected),
                           button("Clear", slot=self._r_clear_ask, tip="Empty the form without saving anything"),
                           None, self.r_total,
                           button("Save receiving voucher", "primary", self.save_receive)))
        self.r_lines.changed.connect(self._r_total)
        for _ in range(3):
            self.r_lines.add_row()
        return w

    def _r_item(self, table, r, item):
        """A new item on the line: start in its buying unit (Packs & cases) at its last cost for that unit."""
        if item["purchase_unit"]:
            table.set_cell(r, "unit", item["purchase_unit"])
        _code, factor = table.unit_of(r)
        table.set_cell(r, "unit_cost", round((item["last_cost"] or item["cost_avg"]) * factor))
        fresh = self.s.db.val("SELECT on_hand FROM items WHERE id = ?", (item["id"],), 0)
        table.set_cell(r, "info", f"{fmt_qty(fresh)} {item['unit']}")

    @staticmethod
    def _r_unit(table, r, item, unit):
        table.set_cell(r, "unit_cost", round((item["last_cost"] or item["cost_avg"]) * unit.factor))

    def _r_total(self):
        rows = self.r_lines.values()
        total = sum(round(v["qty"] * v["unit_cost"]) for v in rows)
        # line totals (rows without an item are skipped by values(), so walk the table itself)
        for r in range(self.r_lines.rowCount()):
            item_id = self.r_lines.cellWidget(r, 0).value()
            if not item_id:
                self.r_lines.set_cell(r, "line", "")
                continue
            qty = self.r_lines.cellWidget(r, 2).value()
            cost = self.r_lines.cellWidget(r, 3).cents()
            self.r_lines.set_cell(r, "line", usd(round(qty * cost)) if qty else "")
        n = sum(1 for v in rows if v["qty"] > 0)
        self.r_total.setText(f"{n} line(s)   Total {usd(total)}")

    def _r_clear_ask(self):
        if (self.r_lines.values() or self.r_bill.text().strip()) and not confirm(
                self, "Clear this receiving voucher? Nothing has been saved yet."):
            return
        self._r_clear()

    def _r_clear(self):
        self.r_po = None
        self.r_po_label.setText("")
        self.r_bill.clear()
        self.r_lines.clear_rows()
        for _ in range(3):
            self.r_lines.add_row()
        self._r_total()

    @guarded
    def load_po(self):
        pos = self.s.db.dicts("SELECT po.*, v.name AS vendor FROM purchase_orders po LEFT JOIN vendors v"
                              " ON v.id = po.vendor_id WHERE po.status IN ('draft','ordered') ORDER BY po.id DESC")
        if not pos:
            info(self, "No open purchase orders.")
            return
        d = PickDialog(self, "Choose a purchase order", ["PO", "Vendor", "Status", "Created"],
                       [[p["po_no"], p["vendor"] or "", p["status"], p["created_at"][:10]] for p in pos],
                       [p["id"] for p in pos])
        if d.exec() != QDialog.Accepted:
            return
        self._fill_from_po(d.choice())

    def _fill_from_po(self, po_id):
        self._refresh_items()
        po = self.s.db.one("SELECT * FROM purchase_orders WHERE id = ?", (po_id,))
        self.r_po = po_id
        self.r_po_label.setText(f"Receiving {po['po_no']} (marked received when saved)")
        if po["vendor_id"]:
            self.r_vendor.set_value(po["vendor_id"])
        self.r_lines.clear_rows()
        for ln in self.s.db.all("SELECT * FROM po_lines WHERE po_id = ? ORDER BY id", (po_id,)):
            r = self.r_lines.add_row({"item_id": ln["item_id"], "qty": ln["qty"], "unit": ln["unit"]})
            self.r_lines.set_cell(r, "unit_cost", round(ln["est_cost"] * float(ln["unit_factor"] or 1))
                                  if ln["unit"] else round(ln["est_cost"]))
        if "receive" in self._tab_keys:
            self.tabs.setCurrentIndex(self._tab_keys.index("receive"))
        self._r_total()

    def _cost_jumps(self, lines) -> list[str]:
        out = []
        for ln in lines:
            item = self.s.db.one("SELECT name, unit, last_cost, cost_avg FROM items WHERE id = ?", (ln["item_id"],))
            ref = float(item["last_cost"] or item["cost_avg"] or 0)
            if ref <= 0:
                continue
            per_base = ln["unit_cost"] / (ln["unit_factor"] or 1)
            if abs(per_base - ref) / ref > COST_JUMP:
                out.append(f"{item['name']}: ${fmt_money(round(per_base))} per {item['unit']} "
                           f"(last ${fmt_money(round(ref))})")
        return out

    @guarded
    def save_receive(self):
        vendor_id = self.r_vendor.value()
        if not vendor_id:
            error(self, "Choose the vendor")
            return
        lines = [{"item_id": v["item_id"], "qty": v["qty"], "unit": v["unit"], "unit_factor": v["unit_factor"],
                  "unit_cost": v["unit_cost"]} for v in self.r_lines.values() if v["qty"] > 0]
        if not lines:
            error(self, "Enter the quantities received")
            return
        bill_no = self.r_bill.text().strip()
        if bill_no and self.s.db.val("SELECT 1 FROM purchases WHERE vendor_id = ? AND lower(bill_no) = lower(?)",
                                     (vendor_id, bill_no)):
            if not confirm(self, f"Invoice {bill_no} from this vendor was already received. Receive it AGAIN?\n\n"
                                 "Press No if this is the same invoice (it would count the stock twice)."):
                return
        zero = [ln for ln in lines if ln["unit_cost"] == 0]
        if zero and not confirm(self, f"{len(zero)} line(s) have a cost of $0.00 (free goods). Save anyway?"):
            return
        jumps = self._cost_jumps(lines)
        if jumps and not confirm(self, "These costs are very different from the last cost. Typing mistake?\n\n" +
                                 "\n".join(jumps[:10]) + "\n\nSave anyway?"):
            return
        paid = self.r_paid.value()
        if paid == "1000" and self.s.shifts.current() is None:
            error(self, "No drawer is open on this station, so it cannot pay from Cash in registers. "
                        "Choose Cash in safe or Checking, or leave it on account.")
            return
        total = sum(round(ln["qty"] * ln["unit_cost"]) for ln in lines)
        if not confirm(self, f"Receive {len(lines)} line(s), total {usd(total)}, from "
                             f"{self.r_vendor.currentText()}?"):
            return
        pid = self.s.purchasing.receive(vendor_id, lines, self.win.user.id, bill_no, self.r_date.iso(), paid,
                                        po_id=self.r_po)
        if paid == "1000":
            self.win.open_drawer()
        doc = self.s.db.one("SELECT doc_no, total FROM purchases WHERE id = ?", (pid,))
        self.win.toast(f"Received {doc['doc_no']} for {usd(doc['total'])}. On hand updated.")
        self._r_clear()
        self.reload()

    # ---- return to vendor --------------------------------------------
    def _vreturn_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.addWidget(label("Send goods back to a vendor (breakage they credit, wrong items, recalls). The items "
                            "leave the on-hand count when this is saved. The vendor's credit is taken off what you "
                            "owe them, or recorded as money received.", "muted", wrap=True))
        self.x_vendor = RefCombo([])
        self.x_ref = QLineEdit()
        self.x_ref.setPlaceholderText("Vendor's credit memo / RMA # (optional)")
        self.x_date = DateEdit()
        self.x_refund = RefCombo([])
        self.x_note = QLineEdit()
        form = QFormLayout()
        form.addRow("Vendor *", self.x_vendor)
        form.addRow("Credit memo / RMA #", self.x_ref)
        form.addRow("Date", self.x_date)
        form.addRow("Credit", self.x_refund)
        form.addRow("Note", self.x_note)
        lay.addLayout(form)
        self.x_scan = QLineEdit()
        self.x_scan.setPlaceholderText("Scan or type a UPC / item #, then Enter")
        self.x_scan.returnPressed.connect(lambda: self._scan_to(self.x_lines, self.x_scan))
        lay.addWidget(self.x_scan)
        self.x_lines = self._lines([("item_id", "Item", "item"), ("unit", "Unit", "unit"), ("qty", "Qty", "qty"),
                                    ("unit_cost", "Credit per unit ($)", "money"), ("info", "On hand now", "readonly")],
                                   self._r_item, self._r_unit)
        lay.addWidget(self.x_lines, 1)
        self.x_total = label("", "sectionTitle")
        self.x_lines.changed.connect(lambda: self.x_total.setText(
            "Credit " + usd(sum(round(v["qty"] * v["unit_cost"]) for v in self.x_lines.values()))))
        lay.addLayout(hbox(button("Add line", slot=lambda: self.x_lines.add_row()),
                           button("Remove line", slot=self.x_lines.remove_selected),
                           button("Clear", slot=self._x_clear_ask, tip="Empty the form without saving anything"),
                           None, self.x_total, button("Save return to vendor", "primary", self.save_vreturn)))
        for _ in range(3):
            self.x_lines.add_row()
        return w

    def _x_clear_ask(self):
        if self.x_lines.values() and not confirm(self, "Clear this return? Nothing has been saved yet."):
            return
        self._x_clear()

    def _x_clear(self):
        self.x_lines.clear_rows()
        self.x_ref.clear()
        self.x_note.clear()
        for _ in range(3):
            self.x_lines.add_row()
        self.x_total.setText("")

    @guarded
    def save_vreturn(self):
        vendor_id = self.x_vendor.value()
        if not vendor_id:
            error(self, "Choose the vendor")
            return
        lines = [{"item_id": v["item_id"], "qty": v["qty"], "unit": v["unit"], "unit_cost": v["unit_cost"]}
                 for v in self.x_lines.values() if v["qty"] > 0]
        if not lines:
            error(self, "Enter the quantities going back")
            return
        refund = self.x_refund.value()
        if refund == "1000" and self.s.shifts.current() is None:
            error(self, "No drawer is open on this station, so a cash refund cannot go into Cash in registers.")
            return
        total = sum(round(ln["qty"] * ln["unit_cost"]) for ln in lines)
        if not confirm(self, f"Return {len(lines)} line(s) to {self.x_vendor.currentText()} for a credit of "
                             f"{usd(total)}? The items leave on hand now."):
            return
        pid = self.s.purchasing.vendor_return(vendor_id, lines, self.win.user.id, self.x_ref.text().strip(),
                                              self.x_date.iso(), refund, self.x_note.text().strip())
        if refund == "1000":
            self.win.open_drawer()
        doc = self.s.db.one("SELECT doc_no, total FROM purchases WHERE id = ?", (pid,))
        self.win.toast(f"Returned {doc['doc_no']}: credit {usd(doc['total'])}. On hand updated.")
        self._x_clear()
        self.reload()

    # ---- purchase orders ---------------------------------------------
    def _po_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.addLayout(hbox(label("Purchase orders. The reorder helper drafts them from items at or below their "
                                 "reorder point.", "muted"), None,
                           button("New PO", "primary", self.new_po),
                           button("Draft POs from low stock", slot=self.po_from_low),
                           button("Mark ordered", slot=lambda: self.po_status("ordered")),
                           button("Receive this PO", slot=self.po_receive),
                           button("Cancel PO", "danger", lambda: self.po_status("cancelled"))))
        self.po_grid = DataGrid([Col("po_no", "PO", width=90), Col("created_at", "Created", "datetime", width=140),
                                 Col("vendor", "Vendor", width=180), Col("status", "Status", width=90),
                                 Col("lines", "Items", width=380), Col("est", "Estimated cost", "money"),
                                 Col("by", "Created by", width=110)], key="pos", export_title="Purchase orders")
        lay.addWidget(self.po_grid, 1)
        return w

    def reload_pos(self):
        if not hasattr(self, "po_grid"):
            return
        rows = self.s.db.dicts(
            "SELECT po.*, v.name AS vendor, CASE WHEN po.created_by_agent = 1 THEN 'Reorder helper' ELSE u.username END"
            " AS by,"
            " (SELECT string_agg(i.name || ' ' || pl.qty || ' ' || COALESCE(pl.unit, i.unit), ', ' ORDER BY pl.id)"
            "  FROM po_lines pl JOIN items i ON i.id = pl.item_id WHERE pl.po_id = po.id) AS lines,"
            " (SELECT CAST(ROUND(SUM(pl.qty * pl.est_cost * COALESCE(pl.unit_factor, 1))) AS BIGINT)"
            "  FROM po_lines pl WHERE pl.po_id = po.id) AS est"
            " FROM purchase_orders po LEFT JOIN vendors v ON v.id = po.vendor_id LEFT JOIN users u ON u.id = po.user_id"
            " ORDER BY CASE po.status WHEN 'draft' THEN 0 WHEN 'ordered' THEN 1 ELSE 2 END, po.id DESC LIMIT 500")
        self.po_grid.set_rows(rows)

    @guarded
    def new_po(self):
        self._refresh_items()
        d = NewPODialog(self, self)
        if d.exec() == QDialog.Accepted:
            self.win.toast("Purchase order saved.")
            self.reload_pos()

    @guarded
    def po_from_low(self):
        res = self.s.agents.run("reorder", "manual")
        self.reload_pos()
        self.win.toast(res)

    @guarded
    def po_status(self, status):
        r = self.po_grid.current_row()
        if r is None:
            error(self, "Click the purchase order first")
            return
        if status != "cancelled" or confirm(self, f"Cancel {r['po_no']}?"):
            self.s.purchasing.set_po_status(r["id"], status, self.win.user.id)
            self.reload_pos()

    @guarded
    def po_receive(self):
        r = self.po_grid.current_row()
        if r is None:
            error(self, "Click the purchase order first")
            return
        if r["status"] not in ("draft", "ordered"):
            error(self, f"{r['po_no']} is {r['status']}")
            return
        if "receive" not in self._tab_keys:
            error(self, "Your role cannot receive inventory")
            return
        self._fill_from_po(r["id"])

    # ---- breakage & waste --------------------------------------------
    def _waste_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        self.w_reason = QComboBox()
        for r in self.s.db.all("SELECT name FROM lookups WHERE list_name = 'waste_reason' AND active = 1 ORDER BY sort"):
            self.w_reason.addItem(r["name"])
        self.w_note = QLineEdit()
        form = QFormLayout()
        form.addRow("Reason", self.w_reason)
        form.addRow("Note", self.w_note)
        lay.addLayout(form)
        self.w_scan = QLineEdit()
        self.w_scan.setPlaceholderText("Scan or type a UPC / item #, then Enter")
        self.w_scan.returnPressed.connect(lambda: self._scan_to(self.w_lines, self.w_scan))
        lay.addWidget(self.w_scan)
        self.w_lines = self._lines([("item_id", "Item", "item"), ("unit", "Unit", "unit"), ("qty", "Qty", "qty"),
                                    ("info", "Cost", "readonly")],
                                   lambda t, r, it: t.set_cell(r, "info", f"${fmt_money(round(it['cost_avg']))} "
                                                                          f"per {it['unit']}"))
        lay.addWidget(self.w_lines, 1)
        lay.addLayout(hbox(button("Add line", slot=lambda: self.w_lines.add_row()),
                           button("Remove line", slot=self.w_lines.remove_selected),
                           button("Clear", slot=self._w_clear, tip="Empty the form without saving anything"), None,
                           button("Record breakage / waste", "primary", self.save_waste)))
        for _ in range(3):
            self.w_lines.add_row()
        return w

    def _w_clear(self):
        if self.w_lines.values() and not confirm(self, "Clear this form? Nothing has been saved yet."):
            return
        self.w_lines.clear_rows()
        self.w_note.clear()
        for _ in range(3):
            self.w_lines.add_row()

    @guarded
    def save_waste(self):
        lines = [(v["item_id"], round(v["qty"] * v["unit_factor"], 3)) for v in self.w_lines.values() if v["qty"] > 0]
        if not lines:
            error(self, "Enter items and quantities")
            return
        if not confirm(self, f"Take {len(lines)} line(s) off stock as '{self.w_reason.currentText()}'?"):
            return
        did = self.s.stock.waste(lines, self.w_reason.currentText(), self.win.user.id, self.w_note.text().strip())
        val = self.s.db.val("SELECT value FROM adjustments WHERE id = ?", (did,))
        self.win.toast(f"Breakage / waste recorded: {usd(val)} at cost.")
        self.w_lines.clear_rows()
        self.w_note.clear()
        for _ in range(3):
            self.w_lines.add_row()
        self.reload()

    # ---- counts ------------------------------------------------------
    def _count_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        self.opening_mode = QCheckBox("Opening stock mode (first day only: quantity and cost; credits Opening "
                                      "balance equity)")
        self.opening_mode.toggled.connect(self.reload_count)
        self.c_dept = RefCombo([], blank="All departments")
        self.c_dept.currentIndexChanged.connect(lambda *_: self.reload_count())
        lay.addWidget(label("Choose a department (or count everything), then type what you counted. Items with a "
                            "case can be counted as full cases plus loose bottles. Rows left blank are not changed. "
                            "Count when the store is closed, or count one department at a time: sales made while you "
                            "count are already in System qty when you save.", "muted", wrap=True))
        lay.addLayout(hbox(label("Department"), self.c_dept, self.opening_mode, None))
        self.count = DataGrid([Col("item_no", "Item #", width=80), Col("name", "Item", width=220),
                               Col("size", "Size", width=70), Col("unit", "Unit", width=60),
                               Col("on_hand", "System qty", "qty"), Col("big_unit", "Case", width=110),
                               Col("big", "Full cases", "qty", editable=True, width=90),
                               Col("loose", "Loose", "qty", editable=True, width=90),
                               Col("counted", "Counted total", "qty", width=100),
                               Col("cost", "Unit cost ($)", "money", editable=True), Col("diff", "Difference", "qty"),
                               Col("diff_value", "Value change", "money")],
                              key="count", on_edit=self._count_edit, export_title="Count sheet")
        lay.addWidget(self.count, 1)
        lay.addLayout(hbox(button("Import opening stock from Excel / CSV", slot=self.import_opening),
                           button("Clear counts", slot=self._c_clear, tip="Empty every counted quantity without saving"),
                           None, button("Save count", "primary", self.save_count)))
        return w

    def _count_edit(self, row, col, value):
        if col.key in ("big", "loose"):
            try:
                v = None if value in ("", None) else float(str(value).replace(",", ""))
            except ValueError:
                error(self, "Type a number")
                return row.get(col.key)
            if v is not None and v < 0:
                error(self, "A count cannot be negative")
                return row.get(col.key)
            row[col.key] = v
            big, loose = row.get("big"), row.get("loose")
            if big is None and loose is None:
                row["counted"] = None
            else:
                row["counted"] = round((big or 0) * (row.get("_big_factor") or 0) + (loose or 0), 3)
            self._count_diff(row)
            return v
        if not self.opening_mode.isChecked():
            error(self, "The cost is only used for opening stock. A count is valued at each item's average cost "
                        "(the owner can correct an average cost in Item List).")
            return row.get("cost")
        from ...core.money import to_cents
        try:
            v = to_cents(str(value).replace("$", "").replace(",", "") or 0)
        except Exception:  # noqa: BLE001
            error(self, "Type the cost like 12.99")
            return row.get("cost")
        row["cost"] = v
        self._count_diff(row)
        return v

    @staticmethod
    def _count_diff(row):
        c = row.get("counted")
        row["diff"] = None if c is None else round(c - row["on_hand"], 3)
        row["diff_value"] = None if c is None else round((c - row["on_hand"]) * (row["cost"] or 0))

    def reload_count(self):
        if not hasattr(self, "count"):
            return
        dept = self.c_dept.value()
        sql = ("SELECT id, item_no, name, size, unit, on_hand, CAST(ROUND(CASE WHEN cost_avg > 0 THEN cost_avg"
               " ELSE last_cost END) AS BIGINT) AS cost FROM items WHERE active = 1 AND kind = 'inventory'")
        args: list = []
        if dept:
            sql += " AND department_id = ?"
            args.append(dept)
        rows = self.s.db.dicts(sql + " ORDER BY name", args)
        big = {}
        for u in self.s.db.all("SELECT item_id, unit, factor FROM item_units WHERE factor > 1 ORDER BY item_id,"
                               " factor DESC"):
            big.setdefault(u["item_id"], (u["unit"], float(u["factor"])))      # the biggest unit of each item
        for r in rows:
            r["counted"] = r["big"] = r["loose"] = None
            r["diff"] = r["diff_value"] = None
            if r["id"] in big:
                r["big_unit"] = f"{big[r['id']][0]} = {big[r['id']][1]:g} {r['unit']}"
                r["_big_factor"] = big[r["id"]][1]
            else:
                r["big_unit"], r["_big_factor"] = "", 0
        self.count.set_rows(rows)

    def _c_clear(self):
        if any(r.get("counted") is not None for r in self.count.model.rows) and not confirm(
                self, "Clear every counted quantity on this sheet? Nothing has been saved yet."):
            return
        self.reload_count()

    @guarded
    def save_count(self):
        rows = [r for r in self.count.model.rows if r.get("counted") is not None]
        if not rows:
            error(self, "Enter at least one counted quantity")
            return
        if self.opening_mode.isChecked():
            if self.s.db.val("SELECT 1 FROM adjustments WHERE kind = 'opening' LIMIT 1") and not confirm(
                    self, "Opening stock was already entered once. Add MORE opening stock?"):
                return
            value = sum(round(r["counted"] * (r["cost"] or 0)) for r in rows)
            if not confirm(self, f"Add opening stock for {len(rows)} item(s), value {usd(value)}? "
                                 "Use this only when starting with OwnPOS."):
                return
            self.s.stock.opening([(r["id"], r["counted"], r["cost"]) for r in rows], self.win.user.id)
        else:
            change = sum(r["diff_value"] or 0 for r in rows)
            if not confirm(self, f"Update stock for {len(rows)} counted item(s)? Value change {usd(change)} "
                                 "(posted to shrinkage). The difference is worked out again at the moment of saving."):
                return
            self.s.stock.count({r["id"]: r["counted"] for r in rows}, self.win.user.id)
        self.win.toast("Stock updated.")
        self.reload()

    @guarded
    def import_opening(self):
        from PySide6.QtWidgets import QFileDialog
        from ...core.excel import read_opening_stock
        path, _ = QFileDialog.getOpenFileName(self, "Opening stock file", "", "Excel or CSV (*.xlsx *.xls *.csv)")
        if not path:
            return
        lines, problems = read_opening_stock(path, self.s.db)
        if not lines:
            error(self, "Nothing to import.\n" + "\n".join(problems[:10]))
            return
        value = sum(round(q * c) for _, q, c in lines)
        msg = f"Add opening stock for {len(lines)} item(s), value {usd(value)}?"
        if problems:
            msg += f"\n\n{len(problems)} row(s) skipped:\n" + "\n".join(problems[:8])
        if confirm(self, msg):
            self.s.stock.opening(lines, self.win.user.id)
            self.win.toast("Opening stock added.")
            self.reload()

    # ---- vendor bills & payments ---------------------------------------
    def _pay_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(12, 12, 12, 12)
        self.vp_grid = DataGrid([Col("name", "Vendor", width=220), Col("phone", "Phone", width=120),
                                 Col("owed", "We owe", "money"), Col("overdue", "Overdue", "money"),
                                 Col("last", "Last invoice", "date", width=110)],
                                key="vpay", export_title="Vendor balances")
        self.vp_grid.activated.connect(lambda _r: self._show_bills())
        self.vp_bills = DataGrid([Col("doc_no", "Voucher", width=100), Col("bill_no", "Invoice #", width=110),
                                  Col("bill_date", "Invoice date", "date", width=100),
                                  Col("due_date", "Due", "date", width=100), Col("total", "Amount", "money"),
                                  Col("open", "Still open", "money")], key="vpay_bills", export_title="Open bills")
        self.vp_amount = MoneyEdit(0)
        self.vp_from = RefCombo(money_accounts(self.s.db))
        self.vp_from.set_value("1100")
        self.vp_ref = QLineEdit()
        self.vp_ref.setPlaceholderText("Check # / reference")
        self.vp_date = DateEdit()
        lay.addWidget(label("Click a vendor to see its open bills (payments are applied to the oldest bill first).",
                            "muted"))
        lay.addWidget(self.vp_grid, 2)
        lay.addWidget(self.vp_bills, 1)
        lay.addLayout(hbox(label("Pay the selected vendor"), self.vp_amount, self.vp_from, self.vp_ref, self.vp_date,
                           button("Record payment", "primary", self.pay_vendor),
                           button("Clear", slot=self._vp_clear), None))
        return w

    def reload_vp(self):
        if not hasattr(self, "vp_grid"):
            return
        from ...core.db import today_str
        today = today_str()
        bills = self.s.purchasing.open_bills()
        overdue: dict = {}
        for b in bills:
            if (b["due_date"] or b["bill_date"]) < today:
                overdue[b["vendor_id"]] = overdue.get(b["vendor_id"], 0) + b["open"]
        rows = self.s.db.dicts(
            "SELECT v.id, v.name, v.phone, COALESCE(SUM(l.credit - l.debit),0) AS owed,"
            " (SELECT MAX(bill_date) FROM purchases p WHERE p.vendor_id = v.id) AS last FROM vendors v"
            " LEFT JOIN journal_lines l ON l.party_type = 'vendor' AND l.party_id = v.id AND l.account = '2000'"
            " WHERE v.active = 1 GROUP BY v.id ORDER BY 4 DESC, v.name")
        for r in rows:
            r["overdue"] = overdue.get(r["id"], 0)
            if r["overdue"]:
                r["_bg"] = "#FEF3F2"
        self.vp_grid.set_rows(rows)
        self._show_bills()

    def _show_bills(self):
        r = self.vp_grid.current_row()
        self.vp_bills.set_rows(self.s.purchasing.open_bills(r["id"]) if r else [])

    def _vp_clear(self):
        self.vp_amount.set_cents(0)
        self.vp_ref.clear()
        from datetime import date as _date
        from ...core.db import today_str
        self.vp_date.set_date(_date.fromisoformat(today_str()))

    @guarded
    def pay_vendor(self):
        r = self.vp_grid.current_row()
        if r is None:
            error(self, "Click the vendor first")
            return
        amt = self.vp_amount.cents()
        if amt <= 0:
            error(self, "Enter the amount paid")
            return
        acct = self.vp_from.value()
        if acct == "1000" and self.s.shifts.current() is None:
            error(self, "No drawer is open on this station, so it cannot pay from Cash in registers.")
            return
        if amt > r["owed"] and not confirm(self, f"{usd(amt)} is more than the {usd(r['owed'])} owed to {r['name']}. "
                                                 "The extra stays as a credit with the vendor. Continue?"):
            return
        if not confirm(self, f"Pay {usd(amt)} to {r['name']} from {self.vp_from.currentText().replace('Paid from ', '')}?"):
            return
        self.s.purchasing.pay_vendor(r["id"], amt, acct, self.win.user.id, self.vp_ref.text().strip(),
                                     pay_date=self.vp_date.iso())
        if acct == "1000":
            self.win.open_drawer()
        self.vp_amount.set_cents(0)
        self.vp_ref.clear()
        self.reload_vp()
        self.win.toast(f"Paid {usd(amt)} to {r['name']}.")

    # ---- refresh -----------------------------------------------------
    def on_show(self):
        if getattr(self, "_fresh", False):
            self._fresh = False
        else:
            self.reload()
        QTimer.singleShot(0, lambda: self.r_scan.setFocus() if hasattr(self, "r_scan") else None)

    def reload(self):
        self._refresh_items()
        if hasattr(self, "r_vendor"):
            current = self.r_vendor.value()
            vendors = self.s.db.all("SELECT id, name FROM vendors WHERE active = 1 ORDER BY name")
            self.r_vendor.set_choices([(v["id"], v["name"]) for v in vendors], blank="Choose vendor…")
            if current:
                self.r_vendor.set_value(current)
            paid = self.r_paid.value() if self.r_paid.count() else None
            self.r_paid.set_choices([(None, "Not paid yet (on account with the vendor)")] + money_accounts(self.s.db))
            self.r_paid.set_value(paid)
        if hasattr(self, "x_vendor"):
            current = self.x_vendor.value()
            vendors = self.s.db.all("SELECT id, name FROM vendors WHERE active = 1 ORDER BY name")
            self.x_vendor.set_choices([(v["id"], v["name"]) for v in vendors], blank="Choose vendor…")
            if current:
                self.x_vendor.set_value(current)
            refund = self.x_refund.value() if self.x_refund.count() else None
            self.x_refund.set_choices([(None, "Credit on our account with the vendor")] +
                                      [(c, t.replace("Paid from", "Money received into")) for c, t in
                                       money_accounts(self.s.db)])
            self.x_refund.set_value(refund)
        if hasattr(self, "c_dept"):
            current = self.c_dept.value()
            self.c_dept.blockSignals(True)
            self.c_dept.set_choices([(d["id"], d["name"]) for d in self.s.db.all(
                "SELECT id, name FROM departments WHERE active = 1 ORDER BY sort, name")], blank="All departments")
            self.c_dept.set_value(current)
            self.c_dept.blockSignals(False)
        self.reload_pos()
        self.reload_count()
        self.reload_vp()


def vbox_buttons(*widgets):
    lay = QVBoxLayout()
    for w in widgets:
        lay.addWidget(w)
    lay.addStretch(1)
    return lay


class NewPODialog(BaseDialog):
    """A purchase order typed by hand: vendor, items, quantities and expected costs."""
    def __init__(self, parent, page: InventoryPage):
        super().__init__(parent, "New purchase order", 860)
        self.page = page
        self.s = page.s
        self.vendor = RefCombo([(v["id"], v["name"]) for v in self.s.db.all(
            "SELECT id, name FROM vendors WHERE active = 1 ORDER BY name")], blank="Choose vendor…")
        self.notes = QLineEdit()
        form = QFormLayout()
        form.addRow("Vendor *", self.vendor)
        form.addRow("Notes", self.notes)
        self.lay.addLayout(form)
        self.scan = QLineEdit()
        self.scan.setPlaceholderText("Scan or type a UPC / item #, then Enter")
        self.lines = page._lines([("item_id", "Item", "item"), ("unit", "Unit", "unit"), ("qty", "Qty", "qty"),
                                  ("est_cost", "Expected cost per unit ($)", "money")],
                                 lambda t, r, it: t.set_cell(r, "est_cost", round(it["last_cost"] or it["cost_avg"])),
                                 lambda t, r, it, u: t.set_cell(r, "est_cost",
                                                                round((it["last_cost"] or it["cost_avg"]) * u.factor)))
        self.scan.returnPressed.connect(lambda: page._scan_to(self.lines, self.scan))
        self.lay.addWidget(self.scan)
        self.lay.addWidget(self.lines, 1)
        self.lay.addLayout(hbox(button("Add line", slot=lambda: self.lines.add_row()),
                                button("Remove line", slot=self.lines.remove_selected), None))
        for _ in range(5):
            self.lines.add_row()
        self.buttons("Save purchase order")
        self.resize(900, 600)

    def validate(self):
        if not self.vendor.value():
            error(self, "Choose the vendor")
            return False
        lines = [v for v in self.lines.values() if v["qty"] > 0]
        if not lines:
            error(self, "Enter at least one item and quantity")
            return False
        try:
            # est_cost is stored per entered unit / unit_factor, like the reorder helper's per-base-unit cost
            self.s.purchasing.create_po(
                self.vendor.value(),
                [{"item_id": v["item_id"], "qty": v["qty"], "unit": v["unit"], "unit_factor": v["unit_factor"],
                  "est_cost": v["est_cost"] / (v["unit_factor"] or 1)} for v in lines],
                self.page.win.user.id, self.notes.text().strip())
        except Exception as exc:  # noqa: BLE001
            error(self, str(exc))
            return False
        return True
