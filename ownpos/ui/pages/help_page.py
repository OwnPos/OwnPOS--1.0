"""Help: the user guide inside the app, and this station's connection to the store server."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QTextCursor, QTextDocument
from PySide6.QtWidgets import QFileDialog, QLineEdit, QTextBrowser, QVBoxLayout, QWidget

from ... import APP_NAME, APP_VERSION
from ..theme import LIGHT, pal
from ..widgets.common import button, guarded, hbox, label, page_header

GUIDE = Path(__file__).resolve().parents[2] / "docs" / "user_guide.md"


def guide_text() -> str:
    try:
        return GUIDE.read_text(encoding="utf-8")
    except OSError:
        return "# User guide\n\nThe guide file is missing from this installation."


def guide_css(c: dict) -> str:
    """Table borders and code boxes for the guide, in the colours of palette c."""
    return (f"table {{ border-collapse: collapse; }} td, th {{ padding: 4px 8px; border: 1px solid {c['field_border']}; }}"
            f" h1 {{ font-size: 20pt; }} h2 {{ font-size: 15pt; margin-top: 18px; }} code {{ background: {c['header']}; }}")


class HelpPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.setObjectName("page")
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        self.find = QLineEdit()
        self.find.setPlaceholderText("Find in the guide…")
        self.find.returnPressed.connect(self.find_next)
        lay.addLayout(page_header("Help", f"{APP_NAME} {APP_VERSION} · press F1 from any screen to open this guide.",
                                  self.find, button("Find next", slot=self.find_next),
                                  button("Print", slot=self.print_guide), button("Save as PDF", slot=self.save_pdf),
                                  button("Connection…", slot=self.connection,
                                         tip="This station's number and the store server it uses")))
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.document().setDefaultStyleSheet(guide_css(pal()))
        self.view.setMarkdown(guide_text())
        lay.addWidget(self.view, 1)
        self.status = label("", "muted")
        lay.addWidget(self.status)
        c = getattr(win.s, "connection", None)
        if c is not None:
            lay.addWidget(label(f"This computer: station {c.station_id} · server {c.host}:{c.port} · database "
                                f"{c.dbname}", "muted"))

    @guarded
    def connection(self):
        """Change this station's server, database or station number. Takes effect after a restart."""
        from ..widgets.common import error, info
        if self.win.user.role != "owner" and not self.win.user.can("settings"):
            error(self, "Only the owner (or someone with the Settings permission) can change the connection.")
            return
        from ..setup_dialogs import ConnectionDialog
        d = ConnectionDialog(getattr(self.win.s, "connection", None), self)
        if d.exec() and d.result_conn is not None:
            info(self, "Saved. Close OwnPOS on this computer and start it again to use the new connection.")

    def find_next(self):
        text = self.find.text().strip()
        if not text:
            return
        if not self.view.find(text):
            cur = self.view.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.Start)
            self.view.setTextCursor(cur)
            self.status.setText("" if self.view.find(text) else f"'{text}' is not in the guide")
        else:
            self.status.setText("")

    def _doc(self) -> QTextDocument:
        doc = QTextDocument()
        doc.setDefaultStyleSheet(guide_css(LIGHT))   # paper is white: print in light colours, even in dark mode
        doc.setMarkdown(guide_text())
        return doc

    @guarded
    def print_guide(self):
        from PySide6.QtPrintSupport import QPrintDialog, QPrinter
        pr = QPrinter(QPrinter.HighResolution)
        if QPrintDialog(pr, self).exec():
            self._doc().print_(pr)

    @guarded
    def save_pdf(self):
        from PySide6.QtPrintSupport import QPrinter
        path, _ = QFileDialog.getSaveFileName(self, "Save the user guide", f"{APP_NAME} user guide.pdf",
                                              "PDF (*.pdf)")
        if not path:
            return
        pr = QPrinter(QPrinter.HighResolution)
        pr.setOutputFormat(QPrinter.PdfFormat)
        pr.setOutputFileName(path)
        self._doc().print_(pr)
        self.win.toast(f"Saved {path}")
