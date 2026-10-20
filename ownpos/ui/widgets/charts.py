"""A tiny bar chart drawn with QPainter (no extra libraries)."""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QWidget

from ...core.money import fmt_money
from ..theme import pal


class BarChart(QWidget):
    def __init__(self, title: str = "", color: str | None = None, highlight_last: bool = False):
        super().__init__()
        self.title = title
        self.color = color          # None = the theme's bar colour
        self.highlight_last = highlight_last
        self.data: list[tuple[str, int]] = []
        self.setMinimumHeight(150)

    def set_data(self, data: list[tuple[str, int]]):
        self.data = data
        self.update()

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = pal()
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(c["panel"]))
        f = QFont(self.font())
        f.setBold(True)
        f.setPointSize(10)
        p.setFont(f)
        p.setPen(QColor(c["ink"]))
        p.drawText(QRectF(12, 6, w - 24, 22), Qt.AlignLeft | Qt.AlignVCenter, self.title)
        f.setBold(False)
        f.setPointSize(8)
        p.setFont(f)
        if not self.data or max(v for _, v in self.data) <= 0:
            p.setPen(QColor(c["muted"]))
            p.drawText(self.rect(), Qt.AlignCenter, "No sales yet")
            return
        top, bottom, left, right = 34, h - 24, 12, w - 12
        mx = max(v for _, v in self.data)
        n = len(self.data)
        slot = (right - left) / n
        bw = max(min(slot * 0.62, 46), 4)
        p.setPen(QPen(QColor(c["chart_grid"]), 1))
        p.drawLine(left, bottom, right, bottom)
        for i, (lbl, v) in enumerate(self.data):
            x = left + i * slot + (slot - bw) / 2
            bh = (bottom - top - 14) * (v / mx) if mx else 0
            col = QColor(c["chart_highlight"] if (self.highlight_last and i == n - 1) else (self.color or c["chart_bar"]))
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawRoundedRect(QRectF(x, bottom - bh, bw, bh), 3, 3)
            p.setPen(QColor(c["chart_axis"]))
            p.drawText(QRectF(left + i * slot, bottom + 3, slot, 16), Qt.AlignHCenter | Qt.AlignTop, lbl)
            if v and (n <= 14 or v == mx):
                txt = fmt_money(v).split(".")[0]
                p.setPen(QColor(c["chart_text"]))
                p.drawText(QRectF(left + i * slot - 10, bottom - bh - 15, slot + 20, 14), Qt.AlignHCenter, txt)
