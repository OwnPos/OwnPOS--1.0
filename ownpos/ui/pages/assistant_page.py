"""Ask OwnPOS: type an everyday question, get the answer from the store's own reports (no AI, no internet).

Only a question the built-in rules do not understand can be passed on to the optional AI fallback (Claude
Haiku, reports-only, off unless the owner switches it on in Settings > AI assistant). The AI never does the
arithmetic either: it can only run the same reports."""
from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import QGridLayout, QLineEdit, QVBoxLayout, QWidget

from ...core.ask import EXAMPLES, answer
from ..widgets.common import button, error, guarded, hbox, label, page_header
from ..widgets.grid import Col, DataGrid


class Worker(QObject):
    done = Signal(str)

    def __init__(self, assistant, question):
        super().__init__()
        self.assistant = assistant
        self.question = question

    def run(self):
        try:
            self.done.emit(self.assistant.ask(self.question))
        except Exception as exc:  # noqa: BLE001
            self.done.emit(f"Sorry, the AI fallback failed: {exc}")


class AssistantPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.setObjectName("page")
        self.win = win
        self.s = win.s
        self.assistant = None
        self.thread = None
        self.last = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.addLayout(page_header("Ask OwnPOS", "Ask about sales, stock, money owed, tax or hours in plain words. "
                                  "Answers come straight from the store's reports, so every number is exact."))
        self.input = QLineEdit()
        self.input.setObjectName("amount")
        self.input.setPlaceholderText("e.g. best sellers in beer last week")
        self.input.returnPressed.connect(self.ask)
        lay.addLayout(hbox(self.input, button("Ask", "primary", self.ask)))
        ex = QGridLayout()
        ex.setSpacing(6)
        for i, q in enumerate(EXAMPLES[:10]):
            ex.addWidget(button(q, slot=lambda _=False, q=q: self._example(q)), i // 5, i % 5)
        lay.addLayout(ex)
        self.answer = label("", "sectionTitle", wrap=True)
        self.answer.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.answer)
        self.open_btn = button("Open in Report Center", slot=self.open_report)
        self.ai_btn = button("Ask the AI fallback instead", slot=self.ask_ai,
                             tip="Sends the question to Claude Haiku (internet). It can only run the same reports.")
        acts = [self.open_btn]
        if win.user.can("export"):
            self.xl_btn = button("Export to Excel", slot=self.export)
            self.pdf_btn = button("Save as PDF", slot=self.save_pdf)
            acts += [self.xl_btn, self.pdf_btn]
        lay.addLayout(hbox(*acts, self.ai_btn, None))
        self.grid = DataGrid([], key="ask_result", toolbar=True)
        lay.addWidget(self.grid, 1)
        self._show_buttons(False)

    def _show_buttons(self, have_report: bool):
        for b in (self.open_btn, getattr(self, "xl_btn", None), getattr(self, "pdf_btn", None)):
            if b is not None:
                b.setVisible(have_report)
        self.ai_btn.setVisible(False)

    def _ai_ready(self) -> bool:
        st = self.s.db.settings()
        return bool(self.win.user.can("assistant") and st.get("ai_enabled") and st.get("ai_api_key"))

    def on_show(self):
        self.input.setFocus()

    def _example(self, q):
        self.input.setText(q)
        self.ask()

    @guarded
    def ask(self):
        q = self.input.text().strip()
        if not q:
            return
        a = answer(self.s.db, q, self.win.user)
        self.last = a
        if a is None:
            self.grid.set_columns([], [])
            self._show_buttons(False)
            msg = ("I don't know how to answer that one yet. Try a question like the examples above (what, and "
                   "for which dates), or open the Report Center.")
            if self._ai_ready():
                msg += " You can also pass it to the AI fallback."
                self.ai_btn.setVisible(True)
            self.answer.setText(msg)
            return
        self.answer.setText(a.text)
        if a.result is None:
            self.grid.set_columns([], [])
            self._show_buttons(False)
            return
        res = a.result
        totals = getattr(res, "totals", True)
        cols = [Col(k, l, t, width=260 if k == "line" else None, total=None if totals else False)
                for k, l, t in res.columns]
        self.grid.show_totals = bool(totals)
        self.grid.set_columns(cols, res.rows)
        self.grid.export_title = res.title
        self._show_buttons(True)

    def open_report(self):
        if self.last is None or self.last.result is None:
            return
        from datetime import date
        from ...core.reports import REGISTRY
        self.win.go("reports")
        page = self.win.pages.get("reports")
        if page is None:
            return
        page.current = REGISTRY[self.last.report]
        page._mem_id = None
        page.title.setText(page.current.title)
        page._show_params(page.current.params)
        p = self.last.params
        if p.get("date_from"):
            page._setting_preset = True
            page.d_from.set_date(date.fromisoformat(p["date_from"]))
            page._setting_preset = False
        if p.get("date_to"):
            page._setting_preset = True
            page.d_to.set_date(date.fromisoformat(p["date_to"]))
            page._setting_preset = False
        page._dates_typed()
        if p.get("department_id"):
            page.dept.set_value(p["department_id"])
        if p.get("item_id"):
            page._ensure_items()
            page.item.set_value(p["item_id"])
        if p.get("user_id"):
            page.user.set_value(p["user_id"])
        if p.get("days"):
            page.days.setValue(int(p["days"]))
        page.run()

    @guarded
    def export(self):
        if self.last is None or self.last.result is None:
            return
        self.grid.export_dialog(self.last.result.title, self.s.db.get_setting("company_name", ""))

    @guarded
    def save_pdf(self):
        if self.last is None or self.last.result is None:
            return
        self.grid.export_pdf_dialog(self.last.result.title, self.s.db.get_setting("company_name", ""),
                                    self.last.text[:200], self.last.result.notes)

    # ---- optional AI fallback -------------------------------------------
    @guarded
    def ask_ai(self):
        q = self.input.text().strip()
        if not q or self.thread is not None:
            return
        if not self._ai_ready():
            error(self, "The AI fallback is off. The owner can switch it on in Settings > AI assistant.")
            return
        from ...core.assistant import Assistant
        if self.assistant is None:
            self.assistant = Assistant(self.s.conninfo, self.s.db.settings(), self.win.user)
        else:
            self.assistant.settings = self.s.db.settings()
        self.answer.setText("Asking the AI fallback…")
        self.ai_btn.setEnabled(False)
        self.thread = QThread()
        self.worker = Worker(self.assistant, q)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.done.connect(self._ai_answer)
        self.thread.start()

    def _ai_answer(self, text):
        self.answer.setText("AI fallback: " + text)
        self.thread.quit()
        self.thread.wait()
        self.thread = None
        self.ai_btn.setEnabled(True)
