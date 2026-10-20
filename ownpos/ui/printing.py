"""Printing receipts and reports through the Windows printer driver, previews,
PDF export, and opening the cash drawer."""
from __future__ import annotations

from PySide6.QtCore import QMarginsF, QSizeF, Qt
from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
from PySide6.QtPrintSupport import QPrintDialog, QPrinter, QPrinterInfo, QPrintPreviewDialog


def printer_names() -> list[str]:
    return [p.printerName() for p in QPrinterInfo.availablePrinters()]


def _make_printer(printer_name: str = "", width_mm: int = 80) -> QPrinter:
    pr = QPrinter(QPrinter.HighResolution)
    if printer_name:
        pr.setPrinterName(printer_name)
    if width_mm:
        printable = width_mm - 8  # typical 72 mm printable on 80 mm paper
        pr.setPageSize(QPageSize(QSizeF(printable, 297), QPageSize.Millimeter, "Receipt"))
        pr.setPageMargins(QMarginsF(1, 2, 1, 2), QPageLayout.Millimeter)
    return pr


def _doc(html: str, width_mm: int) -> QTextDocument:
    doc = QTextDocument()
    doc.setDocumentMargin(2)
    doc.setHtml(html)
    return doc


def print_html(html: str, printer_name: str = "", width_mm: int = 80) -> bool:
    """Print directly to the named printer (or the default). Returns False if no printer exists."""
    if not printer_names():
        return False
    pr = _make_printer(printer_name, width_mm)
    doc = _doc(html, width_mm)
    doc.setPageSize(QSizeF(pr.pageRect(QPrinter.Point).width(), pr.pageRect(QPrinter.Point).height()))
    doc.print_(pr)
    return True


def preview_html(parent, html: str, width_mm: int = 80, title: str = "Print preview") -> None:
    pr = _make_printer("", width_mm)
    doc = _doc(html, width_mm)
    dlg = QPrintPreviewDialog(pr, parent)
    dlg.setWindowTitle(title)
    dlg.paintRequested.connect(lambda p: doc.print_(p))
    dlg.resize(520, 760)
    dlg.exec()


def print_with_dialog(parent, html: str, width_mm: int | None = None) -> None:
    pr = _make_printer("", width_mm or 0)
    dlg = QPrintDialog(pr, parent)
    if dlg.exec():
        _doc(html, width_mm or 0).print_(pr)


def save_pdf(html: str, path: str, width_mm: int = 80) -> str:
    pr = _make_printer("", width_mm)
    pr.setOutputFormat(QPrinter.PdfFormat)
    pr.setOutputFileName(path)
    _doc(html, width_mm).print_(pr)
    return path


def render_png(html: str, path: str, width_px: int = 380) -> str:
    """Used for on-screen receipt previews and testing."""
    from PySide6.QtGui import QImage, QPainter, QColor
    doc = _doc(html, 80)
    doc.setTextWidth(width_px)
    h = int(doc.size().height()) + 10
    img = QImage(width_px, h, QImage.Format_ARGB32)
    img.fill(QColor("white"))
    p = QPainter(img)
    doc.drawContents(p)
    p.end()
    img.save(path)
    return path


def table_pdf(path: str, title: str, subtitle: str, columns: list[tuple], rows: list[dict], totals: dict,
              company: str = "", notes: str = "") -> str:
    """A report as a PDF: Letter paper (landscape when the table is wide), title block, a table whose header
    row repeats on every page, a totals row, notes, and page numbers (added by Qt at the foot of each page).
    rows hold the text to show; numeric columns are right-aligned."""
    from datetime import datetime

    from PySide6.QtGui import (QBrush, QColor, QFont, QTextBlockFormat, QTextCharFormat, QTextCursor,
                               QTextLength, QTextTableFormat)
    numeric = {"money", "qty", "int", "pct", "number", "hours"}
    pr = QPrinter(QPrinter.HighResolution)
    pr.setOutputFormat(QPrinter.PdfFormat)
    pr.setOutputFileName(path)
    pr.setPageSize(QPageSize(QPageSize.Letter))
    pr.setPageOrientation(QPageLayout.Landscape if len(columns) > 6 else QPageLayout.Portrait)
    pr.setPageMargins(QMarginsF(12, 12, 12, 14), QPageLayout.Millimeter)
    doc = QTextDocument()
    base = QFont("Segoe UI", 8 if len(columns) > 9 else 9)
    doc.setDefaultFont(base)
    cur = QTextCursor(doc)
    head = QTextCharFormat()
    head.setFontPointSize(14)
    head.setFontWeight(QFont.Bold)
    cur.insertText(title, head)
    small = QTextCharFormat()
    small.setForeground(QBrush(QColor("#555555")))
    line = " · ".join(x for x in (company, subtitle, f"Printed {datetime.now():%m/%d/%Y %I:%M %p}") if x)
    cur.insertBlock()
    cur.insertText(line, small)
    cur.insertBlock()
    fmt = QTextTableFormat()
    fmt.setHeaderRowCount(1)
    fmt.setCellPadding(3)
    fmt.setCellSpacing(0)
    fmt.setBorder(0.5)
    fmt.setBorderBrush(QBrush(QColor("#BBBBBB")))
    fmt.setWidth(QTextLength(QTextLength.PercentageLength, 100))
    n_rows = 1 + len(rows) + (1 if totals else 0)
    table = cur.insertTable(n_rows, len(columns), fmt)
    bold = QTextCharFormat()
    bold.setFontWeight(QFont.Bold)
    head_bg = QColor("#E8EEF3")

    def put(r, c, text, is_num, char_fmt=None, bg=None):
        cell = table.cellAt(r, c)
        if bg is not None:
            cf = cell.format()
            cf.setBackground(QBrush(bg))
            cell.setFormat(cf)
        cc = cell.firstCursorPosition()
        bf = QTextBlockFormat()
        bf.setAlignment(Qt.AlignRight if is_num else Qt.AlignLeft)
        cc.setBlockFormat(bf)
        cc.insertText(str(text or ""), char_fmt or QTextCharFormat())

    for ci, (key, label_, typ) in enumerate(columns):
        put(0, ci, label_, typ in numeric, bold, head_bg)
    for ri, row in enumerate(rows, start=1):
        style = row.get("style")
        for ci, (key, label_, typ) in enumerate(columns):
            put(ri, ci, row.get(key, ""), typ in numeric, bold if style in ("total", "grand", "header") else None)
    if totals:
        r = n_rows - 1
        for ci, (key, label_, typ) in enumerate(columns):
            text = totals.get(key, "Total" if ci == 0 else "")
            put(r, ci, text, typ in numeric, bold, head_bg)
    if notes:
        cur.movePosition(QTextCursor.End)
        cur.insertBlock()
        cur.insertText(notes, small)
    doc.print_(pr)
    return path
