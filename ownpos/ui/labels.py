"""Shelf tags and barcode labels.

Painted with Qt (one label per page) so it works with any Windows label printer driver (Zebra, DYMO,
Brother...). The barcode is Code 128 of the item's UPC (or its item # when it has no UPC); every
store scanner reads Code 128, and the register looks the code up exactly like a scanned UPC."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QMarginsF, QRectF, QSizeF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPageLayout, QPageSize, QPainter
from PySide6.QtPrintSupport import QPrinter, QPrintPreviewDialog

from ..core.barcode import code128_modules
from ..core.money import fmt_money

# common US label stock, inches -> millimetres
SIZES = {
    '2.25 x 1.25 in (shelf tag)': (57.15, 31.75),
    '2 x 1 in': (50.8, 25.4),
    '1.5 x 1 in': (38.1, 25.4),
    '3 x 1.25 in': (76.2, 31.75),
    '4 x 2 in': (101.6, 50.8),
}
DEFAULT_SIZE = '2.25 x 1.25 in (shelf tag)'


@dataclass
class LabelSpec:
    width_mm: float = 57.15
    height_mm: float = 31.75
    show_shop: bool = True
    show_price: bool = True


def label_for_item(db, item) -> dict:
    """Everything one label needs."""
    code = item["upc"] or item["item_no"] or ""
    name = item["name"] + (f"  {item['size']}" if item["size"] else "")
    return {"shop": db.get_setting("company_name", ""), "name": name,
            "price": f"${fmt_money(item['price1'])}", "code": str(code),
            "foot": f"{code}" + (f"   #{item['item_no']}" if item["item_no"] and item["item_no"] != code else "")}


def _fit_font(base: QFont, text: str, max_w: float, size: float, device, min_size: float = 4.0) -> QFont:
    f = QFont(base)
    s = size
    while s > min_size:
        f.setPointSizeF(s)
        if QFontMetricsF(f, device).horizontalAdvance(text) <= max_w:
            break
        s -= 0.25
    return f


def paint_label(p: QPainter, rect: QRectF, data: dict, spec: LabelSpec, dpi: float) -> None:
    """Draw one label inside rect (device pixels): name and price on top, barcode below, code at the foot."""
    dev = p.device()
    mm = dpi / 25.4
    pad = 1.5 * mm
    r = rect.adjusted(pad, pad, -pad, -pad)
    p.save()
    p.setPen(QColor("black"))
    base = QFont("Segoe UI")
    base.setStyleStrategy(QFont.PreferAntialias)
    k = spec.height_mm / 30.0          # font sizes follow the label height
    y = r.top()

    def line(text: str, size: float, bold: bool = False, align=Qt.AlignLeft) -> None:
        nonlocal y
        if not text:
            return
        f = QFont(base)
        f.setBold(bold)
        f = _fit_font(f, text, r.width(), size * k, dev)
        f.setBold(bold)
        p.setFont(f)
        h = QFontMetricsF(f, dev).height()
        p.drawText(QRectF(r.left(), y, r.width(), h), align | Qt.AlignVCenter, text)
        y += h

    if spec.show_shop and data["shop"] and spec.height_mm >= 28:
        line(data["shop"], 5.0, align=Qt.AlignHCenter)
    line(data["name"], 7.5, bold=True)
    if spec.show_price:
        line(data["price"], 11.0, bold=True)
    foot = data["foot"]
    f_foot = QFont(base)
    f_foot.setPointSizeF(5.0 * k)
    foot_h = QFontMetricsF(f_foot, dev).height()
    bar_top = y + 0.5 * mm
    bar_bottom = r.bottom() - foot_h
    bar_h = max(min(bar_bottom - bar_top, 14 * mm * k), 3 * mm)
    if data["code"]:
        modules = code128_modules(data["code"])
        total = sum(modules) + 20      # 10-module quiet zone each side
        mod_w = min(r.width() / total, 0.40 * mm)   # never wider than a scanner-friendly 0.4 mm
        bar_w = mod_w * (total - 20)
        x = r.left() + (r.width() - bar_w) / 2
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("black"))
        for i, w in enumerate(modules):
            if i % 2 == 0:
                p.drawRect(QRectF(x, bar_top, w * mod_w, bar_h))
            x += w * mod_w
        p.setPen(QColor("black"))
    y = bar_top + bar_h
    p.setFont(_fit_font(base, foot, r.width(), 5.0 * k, dev))
    p.drawText(QRectF(r.left(), y, r.width(), foot_h), Qt.AlignHCenter | Qt.AlignVCenter, foot)
    p.restore()


def _printer(spec: LabelSpec, printer_name: str = "") -> QPrinter:
    pr = QPrinter(QPrinter.HighResolution)
    if printer_name:
        pr.setPrinterName(printer_name)
    pr.setPageSize(QPageSize(QSizeF(spec.width_mm, spec.height_mm), QPageSize.Millimeter, "Label"))
    pr.setPageOrientation(QPageLayout.Portrait)
    pr.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Millimeter)
    pr.setFullPage(True)
    return pr


def _paint_all(pr: QPrinter, labels: list[dict], spec: LabelSpec) -> None:
    p = QPainter(pr)
    try:
        dpi = pr.resolution()
        rect = QRectF(0, 0, spec.width_mm / 25.4 * dpi, spec.height_mm / 25.4 * dpi)
        for i, d in enumerate(labels):
            if i:
                pr.newPage()
            paint_label(p, rect, d, spec, dpi)
    finally:
        p.end()


def print_labels(labels: list[dict], spec: LabelSpec, printer_name: str = "") -> None:
    if labels:
        _paint_all(_printer(spec, printer_name), labels, spec)


def preview_labels(parent, labels: list[dict], spec: LabelSpec, printer_name: str = "") -> None:
    pr = _printer(spec, printer_name)
    dlg = QPrintPreviewDialog(pr, parent)
    dlg.setWindowTitle(f"Labels ({len(labels)})")
    dlg.paintRequested.connect(lambda printer: _paint_all(printer, labels, spec))
    dlg.resize(700, 600)
    dlg.exec()


def render_label_png(data: dict, spec: LabelSpec, path: str, dpi: int = 300) -> str:
    w = int(spec.width_mm / 25.4 * dpi)
    h = int(spec.height_mm / 25.4 * dpi)
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(QColor("white"))
    img.setDotsPerMeterX(int(dpi / 0.0254))
    img.setDotsPerMeterY(int(dpi / 0.0254))
    p = QPainter(img)
    paint_label(p, QRectF(0, 0, w, h), data, spec, dpi)
    p.end()
    img.save(path)
    return path
