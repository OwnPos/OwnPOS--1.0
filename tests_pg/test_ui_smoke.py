"""Every screen opens on a real store database (sample store), as owner and as a cashier, without errors.

Runs Qt offscreen. Message boxes are replaced so a test never waits for a click; any error box shown while
opening a screen fails the test."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def app():
    from ownpos.ui.app import make_app
    return make_app(["test"])


@pytest.fixture
def boxes(monkeypatch):
    """Collect every information / error / question box instead of showing it."""
    shown = []
    from PySide6.QtWidgets import QMessageBox
    from ownpos.ui.widgets import common

    def fake(kind):
        def f(parent, *args, **kw):
            shown.append((kind, " ".join(str(a) for a in args)))
            return QMessageBox.Yes
        return f
    monkeypatch.setattr(common, "error", fake("error"))
    monkeypatch.setattr(common, "info", fake("info"))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(fake("critical")))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fake("warning")))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(fake("information")))
    return shown


def _open_all(win, app):
    from PySide6.QtWidgets import QApplication
    from ownpos.ui.main_window import NAV
    opened = []
    for _g, _h, entries in NAV:
        for key, _label in entries:
            if win._may_open(key):
                win.go(key)
                QApplication.processEvents()
                assert key in win.pages, f"page {key} did not open"
                opened.append(key)
    return opened


def test_every_screen_opens_for_owner_and_cashier(s, app, boxes):
    from ownpos.core.demo import fill_sample
    from ownpos.core.stations import register
    from ownpos.ui.main_window import MainWindow
    register(s.db, 2)
    fill_sample(s, s.owner.id, days=2, sales_per_day=25)
    win = MainWindow(s, s.owner, can_sell=True)
    opened = _open_all(win, app)
    assert len(opened) >= 15
    # each page twice: on_show must also work on an already built page
    _open_all(win, app)
    errors = [b for b in boxes if b[0] in ("error", "critical", "warning")]
    assert not errors, errors
    win.close()

    uid = s.auth.create_user("cash1", "Cash Ier", "cashier", pin="4444")
    win2 = MainWindow(s, s.auth.get(uid), can_sell=True)
    opened2 = _open_all(win2, app)
    assert "register" in opened2 and "settings" not in opened2
    errors = [b for b in boxes if b[0] in ("error", "critical", "warning")]
    assert not errors, errors
    win2.close()


def test_every_report_shows_in_report_center(s, app, boxes):
    from PySide6.QtWidgets import QApplication
    from ownpos.core.demo import fill_sample
    from ownpos.core.reports import REGISTRY
    from ownpos.core.stations import register
    from ownpos.ui.main_window import MainWindow
    register(s.db, 2)
    fill_sample(s, s.owner.id, days=2, sales_per_day=20)
    win = MainWindow(s, s.owner, can_sell=True)
    win.go("reports")
    page = win.pages["reports"]
    for key, rd in REGISTRY.items():
        page.current = rd
        page._show_params(rd.params)
        page._preset("This month")
        page.run()
        QApplication.processEvents()
        assert page.result is not None and page.result.params is not None, key
    errors = [b for b in boxes if b[0] in ("error", "critical", "warning")]
    assert not errors, errors
    win.close()


def test_history_screens_show_details(s, app, boxes):
    from PySide6.QtWidgets import QApplication
    from ownpos.core.demo import fill_sample
    from ownpos.core.stations import register
    from ownpos.ui.main_window import MainWindow
    from ownpos.ui.pages.history_page import ItemHistoryDialog, PartyHistoryDialog
    register(s.db, 2)
    fill_sample(s, s.owner.id, days=3, sales_per_day=30)
    win = MainWindow(s, s.owner, can_sell=True)
    win.go("sales_history")
    page = win.pages["sales_history"]
    page.dates.preset.setCurrentText("This year")
    page.reload()
    assert page.grid.model.rows, "no receipts listed"
    page.grid.view.selectRow(0)
    page._detail()
    assert page.lines.model.rows, "receipt lines not shown"
    total = sum(l["total"] for l in page.lines.model.rows)
    assert total == page.grid.current_row()["total"]          # the lines add up to the receipt
    page.find.setText(page.grid.current_row()["receipt_no"])
    page.reload()
    assert len(page.grid.model.rows) >= 1
    win.go("purchasing_history")
    ph = win.pages["purchasing_history"]
    ph.dates.preset.setCurrentText("This year")
    for i in range(3):
        ph.tabs.setCurrentIndex(i)
        ph.reload()
        QApplication.processEvents()
    item_id = s.db.val("SELECT item_id FROM stock_moves ORDER BY id DESC LIMIT 1")
    d = ItemHistoryDialog(win, s, item_id)
    on_hand = s.db.val("SELECT on_hand FROM items WHERE id = ?", (item_id,))
    d.dates.preset.setCurrentText("This year")
    newest = d.grid.model.rows[0]
    assert abs(newest["balance"] - on_hand) < 1e-6           # running balance ends at today's on hand
    cust = s.db.val("SELECT customer_id FROM sales WHERE customer_id IS NOT NULL LIMIT 1")
    if cust:
        PartyHistoryDialog(win, s, "customer", cust)
    vend = s.db.val("SELECT vendor_id FROM purchases WHERE vendor_id IS NOT NULL LIMIT 1")
    if vend:
        PartyHistoryDialog(win, s, "vendor", vend)
    errors = [b for b in boxes if b[0] in ("error", "critical", "warning")]
    assert not errors, errors
    win.close()


def test_receive_and_return_by_scanning_in_the_screen(s, app, boxes, monkeypatch):
    """Scan a bottle twice and a case once on the receiving form, save; then return one bottle to the vendor."""
    from ownpos.ui.main_window import MainWindow
    from ownpos.ui.pages import inventory_page
    from tests_pg.helpers import beer_with_packs, vendor
    monkeypatch.setattr(inventory_page, "confirm", lambda *a, **k: True)
    iid = beer_with_packs(s)                 # can UPC 018200000164, case barcode 018200000188 (= 24 cans)
    v = vendor(s, "Scan Distributing")
    win = MainWindow(s, s.owner, can_sell=True)
    win.go("inventory")
    page = win.pages["inventory"]
    page.r_vendor.set_value(v)
    page.r_bill.setText("SCAN-1")
    for code in ("018200000164", "018200000164", "018200000188"):
        page.r_scan.setText(code)
        page._scan_to(page.r_lines, page.r_scan)
    vals = page.r_lines.values()
    assert sorted((x["unit"], x["qty"]) for x in vals) == [("can", 2.0), ("case", 1.0)], vals
    for r in range(page.r_lines.rowCount()):
        if page.r_lines.cellWidget(r, 0).value():
            page.r_lines.set_cell(r, "unit_cost", 1800 if page.r_lines.unit_of(r)[0] == "case" else 75)
    page.save_receive()
    assert s.db.val("SELECT on_hand FROM items WHERE id = ?", (iid,)) == 26        # 2 cans + 24
    assert s.purchasing.vendor_balance(v) == 2 * 75 + 1800
    page.x_vendor.set_value(v)
    page.x_scan.setText("018200000164")
    page._scan_to(page.x_lines, page.x_scan)
    page.save_vreturn()
    assert s.db.val("SELECT on_hand FROM items WHERE id = ?", (iid,)) == 25
    errors = [b for b in boxes if b[0] in ("error", "critical", "warning")]
    assert not errors, errors
    win.close()


def test_ask_ownpos_every_example(s, app, boxes):
    from ownpos.core.ask import EXAMPLES
    from ownpos.core.demo import fill_sample
    from ownpos.core.stations import register
    from ownpos.ui.main_window import MainWindow
    register(s.db, 2)
    fill_sample(s, s.owner.id, days=3, sales_per_day=20)
    win = MainWindow(s, s.owner, can_sell=True)
    win.go("assistant")
    page = win.pages["assistant"]
    for q in EXAMPLES:
        page.input.setText(q)
        page.ask()
        assert page.last is not None, f"not understood: {q}"
        assert page.answer.text() and "Nothing" not in page.answer.text()[:3], q
    page.input.setText("Best sellers in beer this month")
    page.ask()
    page.open_report()
    rp = win.pages["reports"]
    assert rp.current.key == "best_sellers" and rp.dept.value() is not None
    page.input.setText("what is the meaning of life")
    page.ask()
    assert page.last is None and "don't know" in page.answer.text()
    errors = [b for b in boxes if b[0] in ("error", "critical", "warning")]
    assert not errors, errors
    win.close()


def test_full_sale_on_the_register_screen(s, app, boxes, monkeypatch):
    """Scan 3 cans and a case, give the employee 10% button, pay exact cash: stock, books and receipt agree."""
    from ownpos.ui.main_window import MainWindow
    from ownpos.ui.pages import register as reg_mod
    from tests_pg.helpers import beer_with_packs, books_ok, receive
    monkeypatch.setattr(reg_mod, "confirm", lambda *a, **k: True)
    s.db.set_setting("sale_hours_check", False)
    iid = beer_with_packs(s)
    receive(s, iid, 48, 75)
    s.shifts.open(s.owner.id, 10000)
    win = MainWindow(s, s.owner, can_sell=True)
    printed = []
    monkeypatch.setattr(win, "print_receipt", lambda sid, force=False: printed.append(sid))
    monkeypatch.setattr(win, "open_drawer", lambda: None)
    win.go("register")
    page = win.pages["register"]
    for code in ("3*018200000164", "018200000188"):
        page.scan.setText(code)
        page.on_scan()
    assert [(ln.unit, ln.qty) for ln in page.cart.lines] == [("can", 3), ("case", 1)]
    page.quick_discount(10, "Employee 10%")
    t = s.sales.totals(page.cart)
    # 3 x $1.29 = $3.87 and a case $22.99; 10% per line rounded half-up: $0.39 + $2.30
    assert t["discount"] == 39 + 230
    net = 387 + 2299 - 269
    assert t["total"] == net + round(net * 0.07)
    page._finish([{"method": "CASH", "tendered": t["total"]}], 0)
    assert printed and not page.cart.lines
    sale = s.db.one("SELECT * FROM sales WHERE id = ?", (printed[0],))
    assert sale["total"] == t["total"] and sale["discount_reason"] == "Employee 10%"
    assert s.db.val("SELECT on_hand FROM items WHERE id = ?", (iid,)) == 48 - 27
    books_ok(s)
    errors = [b for b in boxes if b[0] in ("error", "critical", "warning")]
    assert not errors, errors
    win.close()
