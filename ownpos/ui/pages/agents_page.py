"""Agents: see what each automatic helper does, run it now, switch it on or off,
and review findings and day summaries."""
from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from ..widgets.common import button, guarded, hbox, label, page_header
from ..widgets.grid import Col, DataGrid

KIND = {"helper": "Rule-based helper", "supervisor": "Transaction supervisor", "builder": "Report builder",
        "summary": "Summary writer"}
WHEN = {"startup": "when the app starts", "hourly": "every hour", "day_close": "at each Z-Out",
        "monthly": "after each month", "realtime": "on every receipt"}


class AgentsPage(QWidget):
    def __init__(self, win):
        super().__init__()
        self.setObjectName("page")
        self.win = win
        self.s = win.s
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.addLayout(page_header("Agents", "Automatic helpers that watch the books, build reports and write summaries."
                                  " They only read data and draft documents; they never change a receipt.",
                                  button("Run selected now", "primary", self.run_selected),
                                  button("Switch on / off", slot=self.toggle)))
        tabs = QTabWidget()
        w = QWidget()
        wl = QVBoxLayout(w)
        wl.setContentsMargins(10, 10, 10, 10)
        # long text columns elide; hover a cell for the full text
        self.grid = DataGrid([Col("title", "Agent"), Col("kind", "Kind"), Col("when", "Runs"), Col("enabled", "On"),
                              Col("last", "Last run"), Col("result", "Last result", max_width=380),
                              Col("description", "What it does", stretch=True, max_width=520)],
                             key="agents", toolbar=False, filters=False)
        wl.addWidget(self.grid, 1)
        tabs.addTab(w, "Agents")
        f = QWidget()
        fl = QVBoxLayout(f)
        fl.setContentsMargins(10, 10, 10, 10)
        self.show_done = QCheckBox("Include findings already marked done")
        self.show_done.toggled.connect(self.on_show)
        fl.addLayout(hbox(self.show_done, None, button("Mark selected done", slot=self.ack)))
        self.findings = DataGrid([Col("created_at", "When"), Col("agent", "Agent"), Col("severity", "Level"),
                                  Col("title", "Finding", max_width=380), Col("detail", "Detail", stretch=True, max_width=520),
                                  Col("status", "Status")],
                                 key="findings", export_title="Agent findings")
        fl.addWidget(self.findings, 1)
        tabs.addTab(f, "Findings")
        sm = QWidget()
        sl = QVBoxLayout(sm)
        sl.setContentsMargins(10, 10, 10, 10)
        sl.addLayout(hbox(label("Day summaries written at each Z-Out", "muted"), None,
                          button("Write today's summary now", slot=self.write_summary)))
        self.summaries = QTextBrowser()
        sl.addWidget(self.summaries, 1)
        tabs.addTab(sm, "Summaries")
        lay.addWidget(tabs, 1)

    def on_show(self):
        last = self.s.agents.last_runs()
        rows = []
        for name, a in self.s.agents.agents.items():
            lr = last.get(name)
            rows.append({"name": name, "title": a.title, "kind": KIND.get(a.kind, a.kind),
                         "when": ", ".join(WHEN.get(x, x) for x in a.schedules),
                         "enabled": "Yes" if self.s.agents.enabled(name) else "No",
                         "last": lr["finished_at"] if lr else "", "result": lr["summary"] if lr else "",
                         "description": a.description, "_muted": not self.s.agents.enabled(name)})
        self.grid.set_rows(rows)
        cond = "" if self.show_done.isChecked() else "WHERE acknowledged = 0"
        f = self.s.db.dicts(f"SELECT * FROM agent_findings {cond} ORDER BY id DESC LIMIT 1000")
        for r in f:
            r["status"] = "Done" if r["acknowledged"] else "Open"
            r["_bg"] = {"critical": "#FEF3F2", "warning": "#FFFAEB"}.get(r["severity"])
        self.findings.set_rows(f)
        texts = self.s.db.all("SELECT period, text FROM summaries ORDER BY id DESC LIMIT 60")
        self.summaries.setPlainText("\n\n".join(f"— {t['period']} —\n{t['text']}" for t in texts) or "No summaries yet.")

    @guarded
    def run_selected(self):
        rows = self.grid.selected_rows()
        for r in rows:
            res = self.s.agents.run(r["name"], "manual")
            self.win.toast(f"{r['title']}: {res}")
        self.on_show()

    def toggle(self):
        for r in self.grid.selected_rows():
            self.s.agents.set_enabled(r["name"], r["enabled"] != "Yes")
        self.on_show()

    def ack(self):
        for r in self.findings.selected_rows():
            self.s.agents.acknowledge(r["id"], self.win.user.id)
        self.on_show()

    @guarded
    def write_summary(self):
        self.s.agents.run("summary", "manual")
        self.on_show()
