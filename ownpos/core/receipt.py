"""Receipt, X/Z-Out and Day Closing layouts as HTML (printed by Qt through the Windows printer driver)."""
from __future__ import annotations

import html
from datetime import datetime

from .. import APP_NAME
from .db import Database
from .money import fmt_money, fmt_qty


def _e(s) -> str:
    return html.escape(str(s or ""))


def _us_dt(text: str) -> str:
    try:
        return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").strftime("%m/%d/%Y %I:%M %p")
    except ValueError:
        return text


def _us_d(text: str) -> str:
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%m/%d/%Y")
    except ValueError:
        return text


def _head(st: dict) -> list[str]:
    out = [f"<div style='font-size:12pt;font-weight:bold'>{_e(st.get('company_name', ''))}</div>"]
    for key in ("company_address", "company_city_state_zip", "company_phone"):
        if st.get(key):
            out.append(f"<div>{_e(st[key])}</div>")
    if st.get("receipt_header"):
        out.append(f"<div>{_e(st['receipt_header'])}</div>")
    return out


def receipt_html(db: Database, sale_id: int, copy_no: int = 0, width_mm: int | None = None) -> str:
    st = db.settings()
    width_mm = width_mm or int(st.get("receipt_width_mm", 80))
    small = "8pt" if width_mm <= 58 else "9pt"
    s = db.one("SELECT s.*, u.full_name FROM sales s JOIN users u ON u.id = s.user_id WHERE s.id = ?", (sale_id,))
    lines = db.all("SELECT * FROM sale_lines WHERE sale_id = ? ORDER BY line_no", (sale_id,))
    tenders = db.all("SELECT t.*, m.name, m.is_card FROM sale_tenders t LEFT JOIN payment_methods m"
                     " ON m.code = t.method_code WHERE t.sale_id = ? ORDER BY t.id", (sale_id,))
    station = db.val("SELECT name FROM stations WHERE id = ?", (s["station_id"],), f"Station {s['station_id']}")
    is_return = s["doc_type"] == "CN"
    title = ("VOID" if s["notes"] == "Void" else "RETURN") if is_return else "SALES RECEIPT"
    h = [f"<html><body style=\"font-family:'Segoe UI',Arial,sans-serif;font-size:{small}\">",
         "<div align='center'>", *_head(st),
         f"<div style='font-size:11pt;font-weight:bold;margin-top:4px'>{title}</div>"]
    if copy_no:
        h.append(f"<div style='font-weight:bold'>REPRINT - COPY {copy_no}</div>")
    if st.get("is_demo"):
        h.append("<div style='font-weight:bold'>*** TRAINING - SAMPLE STORE - NOT A REAL SALE ***</div>")
    h.append("</div><hr/><table width='100%' cellspacing='0' cellpadding='0'>")
    meta = [("Receipt #", s["receipt_no"]), ("Date", _us_dt(s["created_at"])), ("Cashier", s["full_name"]),
            ("Station", station)]
    if s["customer_name"]:
        meta.append(("Customer", s["customer_name"]))
    if is_return:
        orig = db.one("SELECT receipt_no, created_at FROM sales WHERE id = ?", (s["ref_sale_id"],))
        if orig:
            meta.append(("Original receipt", f"#{orig['receipt_no']} ({_us_d(orig['created_at'])})"))
        meta.append(("Reason", s["reason"]))
    for k, v in meta:
        h.append(f"<tr><td>{_e(k)}</td><td align='right'>{_e(v)}</td></tr>")
    h.append("</table><hr/><table width='100%' cellspacing='0' cellpadding='1'>")
    for ln in lines:
        gross = ln["net"] + ln["discount"]
        mark = "" if ln["tax_rate"] else " N"         # N = not taxed
        h.append(f"<tr><td colspan='2'>{_e(ln['description'])}</td><td align='right'>"
                 f"{'-' if is_return else ''}{fmt_money(gross)}{mark}</td></tr>")
        if ln["qty"] != 1 or (ln["unit_factor"] or 1) != 1:
            h.append(f"<tr><td colspan='3' style='color:#444'>&nbsp;&nbsp;{fmt_qty(ln['qty'])} {_e(ln['unit'] or '')}"
                     f" @ {fmt_money(ln['price'])}</td></tr>")
        if ln["discount"]:
            h.append(f"<tr><td colspan='2' style='color:#444'>&nbsp;&nbsp;Discount</td>"
                     f"<td align='right'>{'' if is_return else '-'}{fmt_money(ln['discount'])}</td></tr>")
        if ln["price"] != ln["list_price"] and ln["list_price"]:
            h.append(f"<tr><td colspan='3' style='color:#444'>&nbsp;&nbsp;Price changed from "
                     f"{fmt_money(ln['list_price'])}</td></tr>")
    sign = "-" if is_return else ""
    h.append("</table><hr/><table width='100%' cellspacing='0' cellpadding='1'>")
    rows = [("Subtotal", sign + fmt_money(s["subtotal"]))]
    if s["discount"]:
        label = s["discount_reason"] or "Discount"
        rows.append((label, ("" if is_return else "-") + fmt_money(s["discount"])))
    rate = db.val("SELECT rate FROM tax_codes WHERE code = 'IN7'", default=7.0)
    rows.append((f"Sales tax {rate:g}%", sign + fmt_money(s["tax"])))
    for k, v in rows:
        h.append(f"<tr><td>{_e(k)}</td><td align='right'>{v}</td></tr>")
    h.append(f"<tr style='font-size:12pt;font-weight:bold'><td>{'REFUND' if is_return else 'TOTAL'}</td>"
             f"<td align='right'>{sign}${fmt_money(s['total'])}</td></tr></table><hr/>")
    h.append("<table width='100%' cellspacing='0' cellpadding='1'>")
    change = 0
    for t in tenders:
        nm = t["name"] or t["method_code"]
        ref = ""
        if t["ref"]:
            ref = f" ****{_e(t['ref'][-4:])}" if t["is_card"] else f" #{_e(t['ref'])}"
        if t["auth_code"]:
            ref += f" Auth {_e(t['auth_code'])}"
        h.append(f"<tr><td>{'Refund to ' if is_return else ''}{_e(nm)}{ref}</td>"
                 f"<td align='right'>{fmt_money(t['tendered'])}</td></tr>")
        change += t["change"]
    if change:
        h.append(f"<tr style='font-weight:bold'><td>Change</td><td align='right'>{fmt_money(change)}</td></tr>")
    h.append("</table>")
    if not is_return and st.get("receipt_show_savings", True):
        saved = sum(max(round(ln["qty"] * ln["list_price"]) - ln["net"], 0) for ln in lines)
        if saved > 0:
            h.append(f"<div align='center' style='margin-top:3px'>You saved ${fmt_money(saved)}</div>")
    items = sum(ln["qty"] * (ln["unit_factor"] or 1) for ln in lines)
    h.append(f"<div style='margin-top:3px'>Items: {fmt_qty(items)}</div><hr/>")
    if any(not ln["tax_rate"] for ln in lines):
        h.append("<div style='color:#444'>N = not taxed</div>")
    h.append(f"<div align='center'>{_e(st.get('receipt_footer', ''))}</div>")
    if st.get("company_permit_no"):
        h.append(f"<div align='center' style='font-size:7pt'>Permit {_e(st['company_permit_no'])}</div>")
    h.append(f"<div align='center' style='font-size:7pt;color:#666'>{APP_NAME}</div></body></html>")
    return "".join(h)


def zout_html(db: Database, summ: dict, title: str = "Z-OUT") -> str:
    st = db.settings()
    sh = summ["shift"]
    cashier = db.val("SELECT full_name FROM users WHERE id = ?", (sh["user_id"],), "")
    station = db.val("SELECT name FROM stations WHERE id = ?", (sh["station_id"],), f"Station {sh['station_id']}")
    rows = [
        ("Sales receipts", str(summ["invoices"])),
        ("Gross sales", fmt_money(summ["gross"])),
        ("Discounts", "-" + fmt_money(summ["discounts"]) if summ["discounts"] else "0.00"),
        (f"Returns ({summ['returns_count']})", ("-" if summ["returns_total"] else "") + fmt_money(summ["returns_total"])),
        ("Taxable sales (net)", fmt_money(summ["taxable"])),
        ("Non-taxable sales (net)", fmt_money(summ["nontaxable"])),
        ("Sales tax (net of returns)", fmt_money(summ["tax"])),
        ("Net collected", fmt_money(summ["net_sales"])),
    ]
    h = ["<html><body style=\"font-family:'Segoe UI',Arial,sans-serif;font-size:9pt\">",
         f"<div align='center'><b>{_e(st.get('company_name', ''))}</b><br/><b>{title}</b>"
         f"{' ' + _e(summ.get('z_number', '')) if summ.get('z_number') else ''}</div><hr/>",
         f"<div>{_e(station)}<br/>Cashier: {_e(cashier)}<br/>Opened: {_us_dt(sh['opened_at'])}<br/>"
         f"{'Closed: ' + _us_dt(sh['closed_at']) + '<br/>' if sh.get('closed_at') else ''}</div><hr/><table width='100%'>"]
    for k, v in rows:
        h.append(f"<tr><td>{k}</td><td align='right'>{v}</td></tr>")
    if summ.get("discount_lines"):
        h.append("</table><hr/><b>Discounts</b><table width='100%'>")
        for d in summ["discount_lines"]:
            h.append(f"<tr><td>{_e(d['reason'])} ({d['n']})</td><td align='right'>{fmt_money(d['amount'])}</td></tr>")
    h.append("</table><hr/><b>Payments</b><table width='100%'>")
    for t in summ["tenders"]:
        h.append(f"<tr><td>{_e(t['name'])}</td><td align='right'>{fmt_money(t['net'])}</td></tr>")
    h.append("</table><hr/><b>Cash drawer</b><table width='100%'>")
    cash_rows = [(ln["label"], ln["amount"]) for ln in summ["drawer_lines"]
                 if ln["amount"] or ln["key"] in ("opening_float", "cash_sales_net")]
    cash_rows.append(("Expected in drawer", summ["expected_cash"]))
    if "counted_cash" in summ:
        cash_rows += [("Counted", summ["counted_cash"]), ("Over (+) / short (-)", summ["over_short"])]
    for k, v in cash_rows:
        bold = " style='font-weight:bold'" if k.startswith(("Expected", "Over")) else ""
        h.append(f"<tr{bold}><td>{_e(k)}</td><td align='right'>{fmt_money(v)}</td></tr>")
    h.append(f"</table><hr/><div>No-sale drawer opens: {summ['no_sales']}</div></body></html>")
    return "".join(h)


def day_closing_html(db: Database, dc: dict) -> str:
    st = db.settings()
    day = _us_d(dc["date"])
    h = ["<html><body style=\"font-family:'Segoe UI',Arial,sans-serif;font-size:9pt\">",
         f"<div align='center'><b>{_e(st.get('company_name', ''))}</b><br/><b>DAY CLOSING</b><br/>{day}</div><hr/>",
         "<table width='100%'>"]
    rows = [("Sales receipts", str(dc["n_inv"])), ("Returns / voids", str(dc["n_cn"])),
            ("Gross sales", fmt_money(dc["gross"])), ("Discounts", fmt_money(dc["disc"])),
            ("Net sales (after returns)", fmt_money(dc["net"])), ("  taxable", fmt_money(dc["taxable"])),
            ("  not taxable", fmt_money(dc["nontaxable"])), ("SALES TAX", fmt_money(dc["tax"])),
            ("Total collected", fmt_money(dc["sales_total"] - dc["returns_total"]))]
    for k, v in rows:
        bold = " style='font-weight:bold'" if k in ("SALES TAX", "Total collected") else ""
        h.append(f"<tr{bold}><td>{_e(k)}</td><td align='right'>{v}</td></tr>")
    h.append("</table><hr/><b>Payments</b><table width='100%'>")
    for t in dc["tenders"]:
        h.append(f"<tr><td>{_e(t['name'])}</td><td align='right'>{fmt_money(t['net'])}</td></tr>")
    if dc["discount_lines"]:
        h.append("</table><hr/><b>Discounts</b><table width='100%'>")
        for d in dc["discount_lines"]:
            h.append(f"<tr><td>{_e(d['reason'])} ({d['n']})</td><td align='right'>{fmt_money(d['amount'])}</td></tr>")
    h.append("</table><hr/><b>Drawers</b><table width='100%'>")
    for s in dc["stations"]:
        name = s["station_name"] or f"Station {s['station_id']}"
        if s["status"] == "open":
            h.append(f"<tr><td>{_e(name)} ({_e(s['cashier'])})</td><td align='right'><b>STILL OPEN</b></td></tr>")
        else:
            h.append(f"<tr><td>{_e(name)} {_e(s['z_number'])}</td><td align='right'>over/short "
                     f"{fmt_money(s['over_short'] or 0)}</td></tr>")
    h.append(f"</table><hr/><div>Gross profit: {fmt_money(dc['gross_profit'])}</div></body></html>")
    return "".join(h)
