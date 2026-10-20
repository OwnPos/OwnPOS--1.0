"""DataGrid: an Excel-like table used for every list and report.

- click a header to sort; Shift+click to add a second/third sort column
- type in the filter box under any header (supports >100, <5, =exact, !text)
- search box filters across all columns
- right-click a header to show/hide columns (remembered per grid)
- Ctrl+C copies the selection so it pastes straight into Excel
- optional in-place editing with dropdowns and checkboxes
- row count and totals of the visible rows at the bottom
"""
from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import (QAbstractTableModel, QEvent, QModelIndex, QSettings, QSortFilterProxyModel, Qt, QTimer,
                            Signal)
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMenu, QStyledItemDelegate, QTableView, QVBoxLayout, QWidget)

from ...core.money import fmt_money, fmt_qty
from ..branding import ui_settings
from ..theme import pal, row_bg
from .common import button, error, label

NUMERIC = {"money", "qty", "int", "pct", "number", "hours"}
RAW_SORT = {"date", "datetime", "code"}     # sorted by the stored value (ISO dates), shown the U.S. way


def us_date(value) -> str:
    text = str(value)
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return f"{text[5:7]}/{text[8:10]}/{text[0:4]}"
    return text


def us_datetime(value) -> str:
    text = str(value)
    if len(text) >= 16 and text[4] == "-" and text[10] == " ":
        h, m = int(text[11:13]), text[14:16]
        return f"{us_date(text)} {(h % 12) or 12}:{m} {'AM' if h < 12 else 'PM'}"
    return us_date(text)


@dataclass
class Col:
    key: str
    label: str
    type: str = "text"
    editable: bool = False
    choices: list | None = None     # [(value, label)]
    width: int | None = None        # starting width; the header text always fits
    hidden: bool = False
    total: bool | None = None       # None = automatic
    stretch: bool = False           # this column takes any spare width (default: the main text column)
    max_width: int | None = None    # cap for automatic sizing (long text elides, full text in the tooltip)
    on_new: object = None           # Type for New: function text -> (value, label) or None (see ui/typenew.py)
    _map: dict = field(default_factory=dict, repr=False)

    def __post_init__(self):
        if self.choices:
            self._map = {str(v): lbl for v, lbl in self.choices}


def as_cols(columns) -> list[Col]:
    out = []
    for c in columns:
        if isinstance(c, Col):
            out.append(c)
        else:
            key, lbl, typ = c[:3]
            out.append(Col(key, lbl, typ))
    return out


def display(col: Col, value) -> str:
    if value is None:
        return ""
    t = col.type
    if col.choices is not None or t == "choice":
        return str(col._map.get(str(value), value))
    if t == "money":
        return fmt_money(value)
    if t == "qty":
        return fmt_qty(value)
    if t == "int":
        try:
            return f"{int(value):,}"
        except (TypeError, ValueError):
            return str(value)
    if t == "pct":
        try:
            return f"{float(value):.1f}%"
        except (TypeError, ValueError):
            return str(value)
    if t in ("number", "hours"):
        try:
            return f"{float(value):,.2f}"
        except (TypeError, ValueError):
            return str(value)
    if t == "date":
        return us_date(value)
    if t == "datetime":
        return us_datetime(value)
    if t == "bool":
        return ""
    return str(value)


class GridModel(QAbstractTableModel):
    def __init__(self, columns: list[Col], rows: list[dict] | None = None, on_edit=None):
        super().__init__()
        self.cols = columns
        self.rows = rows or []
        self.on_edit = on_edit

    def set_rows(self, rows):
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.cols)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return self.cols[section].label
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        col = self.cols[index.column()]
        v = row.get(col.key)
        if role == Qt.DisplayRole:
            return display(col, v)
        if role == Qt.EditRole:
            if col.type == "money" and v is not None:
                return f"{v / 100:.2f}"
            return "" if v is None else str(v)
        if role == Qt.UserRole:
            return v
        if role == Qt.ToolTipRole and col.type not in NUMERIC and col.type != "bool":
            text = display(col, v)
            return text if len(text) > 18 else None
        if role == Qt.CheckStateRole and col.type == "bool":
            return Qt.Checked if v else Qt.Unchecked
        if role == Qt.TextAlignmentRole:
            if col.type in NUMERIC:
                return int(Qt.AlignRight | Qt.AlignVCenter)
            return int(Qt.AlignLeft | Qt.AlignVCenter)
        if role == Qt.ForegroundRole:
            if col.type in ("money", "qty", "number", "hours") and isinstance(v, (int, float)) and v < 0:
                return QBrush(QColor(pal()["bad"]))
            if row.get("_muted"):
                return QBrush(QColor(pal()["disabled_ink"]))
        if role == Qt.FontRole and row.get("style") in ("total", "grand", "header"):
            f = QFont()
            f.setBold(True)
            return f
        if role == Qt.BackgroundRole and row.get("_bg"):
            return QBrush(QColor(row_bg(row["_bg"])))
        return None

    def flags(self, index):
        f = Qt.ItemIsEnabled | Qt.ItemIsSelectable
        col = self.cols[index.column()]
        if col.editable and self.on_edit is not None:
            f |= Qt.ItemIsUserCheckable if col.type == "bool" else Qt.ItemIsEditable
        return f

    def setData(self, index, value, role=Qt.EditRole):
        if not index.isValid() or self.on_edit is None:
            return False
        row = self.rows[index.row()]
        col = self.cols[index.column()]
        if role == Qt.CheckStateRole and col.type == "bool":
            value = 1 if (value == Qt.Checked or value == Qt.Checked.value) else 0
        elif role != Qt.EditRole:
            return False
        try:
            new = self.on_edit(row, col, value)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)  # `exc` is cleared when the except block ends; keep the text for the delayed message
            QTimer.singleShot(0, lambda m=msg: error(None, m))
            return False
        row[col.key] = new
        # the edit may also change other cells of the row (totals, differences): repaint the whole row
        self.dataChanged.emit(index.siblingAtColumn(0), index.siblingAtColumn(len(self.cols) - 1))
        return True


class GridProxy(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.col_filters: dict[int, str] = {}
        self.search = ""
        self.keys: list[tuple[int, bool]] = []   # (column, descending)

    def set_col_filter(self, col: int, text: str):
        self.col_filters[col] = text.strip()
        self.invalidateFilter()

    def set_search(self, text: str):
        self.search = text.strip().lower()
        self.invalidateFilter()

    @staticmethod
    def _match(col: Col, raw, shown: str, flt: str) -> bool:
        if not flt:
            return True
        neg = flt.startswith("!")
        if neg:
            flt = flt[1:]
        ok = True
        if col.type in NUMERIC and flt[:1] in "<>=" and raw is not None:
            op = flt[:2] if flt[:2] in (">=", "<=") else flt[:1]
            try:
                num = float(flt[len(op):].replace(",", ""))
                val = float(raw) / 100 if col.type == "money" else float(raw)
                ok = {">": val > num, "<": val < num, ">=": val >= num, "<=": val <= num, "=": abs(val - num) < 1e-9}[op]
            except ValueError:
                ok = flt.lower() in shown.lower()
        elif flt.startswith("="):
            ok = shown.lower() == flt[1:].lower()
        else:
            ok = flt.lower() in shown.lower()
        return not ok if neg else ok

    def filterAcceptsRow(self, source_row, source_parent):
        m: GridModel = self.sourceModel()
        row = m.rows[source_row]
        for ci, flt in self.col_filters.items():
            if flt:
                col = m.cols[ci]
                v = row.get(col.key)
                if not self._match(col, v, display(col, v), flt):
                    return False
        if self.search:
            for col in m.cols:
                if self.search in display(col, row.get(col.key)).lower():
                    return True
            return False
        return True

    def lessThan(self, left, right):
        m: GridModel = self.sourceModel()
        a_row, b_row = m.rows[left.row()], m.rows[right.row()]
        for ci, desc in self.keys or [(left.column(), False)]:
            col = m.cols[ci]
            a, b = a_row.get(col.key), b_row.get(col.key)
            if a == b:
                continue
            if a is None:
                res = True
            elif b is None:
                res = False
            elif col.type in RAW_SORT:
                res = str(a) < str(b)
            elif col.type in NUMERIC or isinstance(a, (int, float)) and isinstance(b, (int, float)):
                try:
                    res = float(a) < float(b)
                except (TypeError, ValueError):
                    res = str(a) < str(b)
            else:
                sa, sb = display(col, a).lower(), display(col, b).lower()
                if sa == sb:
                    continue
                res = sa < sb
            return (not res) if desc else res
        return False


class ChoiceDelegate(QStyledItemDelegate):
    def __init__(self, choices, parent=None, col=None):
        super().__init__(parent)
        self.choices = choices
        self.col = col

    def createEditor(self, parent, option, index):
        from .common import RefCombo
        on_new = None
        if self.col is not None and self.col.on_new is not None:
            col = self.col

            def on_new(text):
                made = col.on_new(text)
                if made:                       # the new entry also shows in this column from now on
                    col.choices.append(made)
                    col._map[str(made[0])] = made[1]
                return made
        return RefCombo(self.choices, blank="", parent=parent, on_new=on_new)

    def setEditorData(self, editor, index):
        raw = index.data(Qt.UserRole)
        for i in range(editor.count()):
            if str(editor.itemData(i)) == str(raw):
                editor.setCurrentIndex(i)
                return

    def setModelData(self, editor, model, index):
        model.setData(index, editor.value(), Qt.EditRole)


class DataGrid(QWidget):
    activated = Signal(dict)
    selection_changed = Signal()

    def __init__(self, columns, rows=None, key: str = "", on_edit=None, toolbar: bool = True,
                 export_title: str = "", filters: bool = True, parent=None):
        super().__init__(parent)
        self.key = key
        self.export_title = export_title
        # False for statements whose rows must not be added up (Profit and Loss, Balance Sheet, lists...)
        self.show_totals = True
        self.model = GridModel(as_cols(columns), rows, on_edit)
        self.proxy = GridProxy()
        self.proxy.setSourceModel(self.model)
        self.view = QTableView()
        self.view.setModel(self.proxy)
        self.view.setAlternatingRowColors(True)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(28)
        self.view.setWordWrap(False)
        self.view.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                  | QAbstractItemView.AnyKeyPressed if on_edit else QAbstractItemView.NoEditTriggers)
        hh = self.view.horizontalHeader()
        hh.setSectionsMovable(True)
        hh.setHighlightSections(False)
        hh.setSortIndicatorShown(False)
        hh.setStretchLastSection(False)
        hh.setMinimumSectionSize(36)
        hh.setTextElideMode(Qt.ElideRight)
        self.view.setTextElideMode(Qt.ElideRight)
        hh.sectionClicked.connect(self._header_clicked)
        hh.setContextMenuPolicy(Qt.CustomContextMenu)
        hh.customContextMenuRequested.connect(self._header_menu)
        self.view.doubleClicked.connect(self._double)
        self.view.selectionModel().selectionChanged.connect(lambda *_: self.selection_changed.emit())
        QShortcut(QKeySequence.Copy, self.view, activated=self.copy_selection)

        self.filter_bar = QWidget()
        self.filter_bar.setFixedHeight(28 if filters else 0)
        self.filter_bar.setVisible(filters)
        self.filter_edits: list[QLineEdit] = []
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search all columns…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._search)
        self.count = label("", "muted")
        self.totals = label("", "muted")
        self.totals.setTextInteractionFlags(Qt.TextSelectableByMouse)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        if toolbar:
            self.toolbar = QHBoxLayout()
            self.toolbar.setSpacing(6)
            self.search.setMaximumWidth(320)
            self.toolbar.addWidget(self.search)
            self.toolbar.addWidget(button("Clear filters", slot=self.clear_filters))
            self.toolbar.addStretch(1)
            if export_title:
                self.toolbar.addWidget(button("Export to Excel", slot=self.export_dialog,
                                              tip="Save exactly what is shown (filters and sort) as an .xlsx file"))
            lay.addLayout(self.toolbar)
        lay.addWidget(self.filter_bar)
        lay.addWidget(self.view, 1)
        foot = QHBoxLayout()
        foot.addWidget(self.count)
        foot.addStretch(1)
        foot.addWidget(self.totals)
        lay.addLayout(foot)

        self._sizing = False          # True while the grid itself sets widths
        self._base: dict[int, int] = {}   # column -> width before spare room is added
        self._min: dict[int, int] = {}    # column -> width that shows the whole header
        self._fill_col: int | None = None
        self._autosize_pending = True
        self._build_filters()
        self._apply_columns()
        hh.sectionResized.connect(self._section_resized)
        self.view.viewport().installEventFilter(self)
        hh.sectionMoved.connect(lambda *_: self._place_filters())
        self.view.horizontalScrollBar().valueChanged.connect(lambda *_: self._place_filters())
        self.proxy.rowsInserted.connect(self._update_footer)
        self.proxy.rowsRemoved.connect(self._update_footer)
        self.proxy.modelReset.connect(self._update_footer)
        self.proxy.layoutChanged.connect(self._update_footer)
        self._update_footer()

    # ---- columns -----------------------------------------------------
    def set_columns(self, columns, rows=None):
        self.model.beginResetModel()
        self.model.cols = as_cols(columns)
        if rows is not None:
            self.model.rows = rows
        self.model.endResetModel()
        self.proxy.keys = []
        self.proxy.col_filters = {}
        self._build_filters()
        self._apply_columns()
        self._update_footer()

    def _settings_key(self):
        return f"grid/{self.key}/hidden" if self.key else None

    def _apply_columns(self):
        hidden = None
        sk = self._settings_key()
        if sk:
            hidden = ui_settings().value(sk)
        for i, c in enumerate(self.model.cols):
            hide = (c.key in hidden) if isinstance(hidden, list) else c.hidden
            self.view.setColumnHidden(i, hide)
            if c.editable and (c.choices is not None):
                self.view.setItemDelegateForColumn(i, ChoiceDelegate(c.choices, self.view, c))
            else:
                self.view.setItemDelegateForColumn(i, QStyledItemDelegate(self.view))
        self._autosize_pending = True
        self.autosize()
        QTimer.singleShot(0, self._place_filters)

    # ---- column widths -----------------------------------------------
    # Columns start wide enough for their header and a sample of their contents,
    # within sensible limits; the main text column soaks up spare width; long
    # text elides with the full value in a tooltip. Widths the user drags are
    # remembered per grid (right-click a header > Reset columns to forget them).
    SAMPLE_ROWS = 200
    PAD = 18          # cell padding + grid line
    HEADER_PAD = 34   # header padding + sort arrow
    MAIN_KEYS = ("name", "description", "title", "detail", "item", "items", "memo", "text")

    def _widths_key(self):
        return f"grid/{self.key}/widths" if self.key else None

    def _saved_widths(self) -> dict:
        wk = self._widths_key()
        if not wk:
            return {}
        v = ui_settings().value(wk)
        return v if isinstance(v, dict) else {}

    def _natural_width(self, i: int, c: Col, fm: QFontMetrics, hfm: QFontMetrics) -> int:
        head = hfm.horizontalAdvance(c.label) + self.HEADER_PAD
        if c.type == "bool":
            content = 40
        else:
            content = 0
            for r in self.model.rows[:self.SAMPLE_ROWS]:
                text = display(c, r.get(c.key))
                if text:
                    content = max(content, fm.horizontalAdvance(text))
            content += self.PAD
        cap = c.max_width or (170 if c.type in NUMERIC else 320)
        return max(head, c.width or 0, min(content, cap), 48)

    def autosize(self):
        """Size columns to header + contents. Called when columns change and on the first data load."""
        if not self.model.cols:
            return
        fm = QFontMetrics(self.view.font())
        hf = QFont(self.view.horizontalHeader().font())
        hf.setBold(True)
        hfm = QFontMetrics(hf)
        saved = self._saved_widths()
        self._base = {}
        self._min = {}
        text_cols = []
        for i, c in enumerate(self.model.cols):
            w = self._natural_width(i, c, fm, hfm)
            self._min[i] = hfm.horizontalAdvance(c.label) + self.HEADER_PAD
            if c.key in saved:
                try:
                    w = max(int(saved[c.key]), 36)
                except (TypeError, ValueError):
                    pass
            self._base[i] = w
            if c.type not in NUMERIC and c.type != "bool" and not self.view.isColumnHidden(i):
                text_cols.append((c.stretch, w, i))
        explicit = [i for s, _w, i in text_cols if s]
        named = [i for _s, _w, i in text_cols if self.model.cols[i].key in self.MAIN_KEYS]
        if explicit:
            self._fill_col = explicit[0]
        elif named:
            self._fill_col = named[0]
        elif text_cols:
            self._fill_col = max(text_cols, key=lambda t: t[1])[2]
        else:
            self._fill_col = None
        if self.model.rows:
            self._autosize_pending = False
        self._fit()

    def _fit(self):
        """Apply base widths, shrink long text columns if that avoids a horizontal scrollbar,
        and give spare room to the main text column."""
        if not self._base:
            return
        avail = self.view.viewport().width() if self.view.isVisible() else 0
        visible = [i for i in self._base if not self.view.isColumnHidden(i)]
        widths = dict(self._base)
        saved = self._saved_widths()
        total = sum(widths[i] for i in visible)
        if avail >= 100 and total > avail:
            # shrink wide automatic text columns towards a floor before resorting to scrolling
            floor = {i: max(120, self._min.get(i, 0)) for i in visible}
            shrinkable = [i for i in visible if self.model.cols[i].type not in NUMERIC
                          and self.model.cols[i].key not in saved and widths[i] > floor[i]]
            excess = total - avail
            room = sum(widths[i] - floor[i] for i in shrinkable)
            if room >= excess:  # only when that removes the scrollbar; otherwise keep text readable
                # trim the widest columns first, down to a common level
                lo, hi = 0, max(widths[i] for i in shrinkable)
                while hi - lo > 1:
                    mid = (lo + hi) // 2
                    cut = sum(max(0, widths[i] - max(mid, floor[i])) for i in shrinkable)
                    lo, hi = (mid, hi) if cut >= excess else (lo, mid)
                for i in shrinkable:
                    widths[i] = min(widths[i], max(lo, floor[i]))
                total = sum(widths[i] for i in visible)
        if avail >= 100 and total < avail and self._fill_col in visible:
            widths[self._fill_col] += avail - total
        self._sizing = True
        try:
            for i in visible:
                if self.view.columnWidth(i) != widths[i]:
                    self.view.setColumnWidth(i, widths[i])
        finally:
            self._sizing = False

    def _section_resized(self, index, old, new):
        user = bool(QGuiApplication.mouseButtons() & Qt.LeftButton)
        if not self._sizing and user and 0 <= index < len(self.model.cols):
            # the user dragged a column edge: remember it, and stop stretching that column
            self._base[index] = new
            if index == self._fill_col:
                self._fill_col = None
            wk = self._widths_key()
            if wk:
                saved = self._saved_widths()
                saved[self.model.cols[index].key] = new
                ui_settings().setValue(wk, saved)
        self._place_filters()

    def _save_hidden(self):
        sk = self._settings_key()
        if sk:
            hidden = [c.key for i, c in enumerate(self.model.cols) if self.view.isColumnHidden(i)]
            ui_settings().setValue(sk, hidden)

    def _header_menu(self, pos):
        menu = QMenu(self)
        menu.addSection("Show columns")
        for i, c in enumerate(self.model.cols):
            act = menu.addAction(c.label)
            act.setCheckable(True)
            act.setChecked(not self.view.isColumnHidden(i))
            act.toggled.connect(lambda on, i=i: (self.view.setColumnHidden(i, not on), self._save_hidden(),
                                                 self.autosize(), self._place_filters()))
        menu.addSeparator()
        menu.addAction("Reset columns", self._reset_columns)
        menu.exec(self.view.horizontalHeader().mapToGlobal(pos))

    def _reset_columns(self):
        for k in (self._settings_key(), self._widths_key()):
            if k:
                ui_settings().remove(k)
        self._apply_columns()

    # ---- filters -----------------------------------------------------
    def _build_filters(self):
        for e in self.filter_edits:
            e.deleteLater()
        self.filter_edits = []
        for i, c in enumerate(self.model.cols):
            e = QLineEdit(self.filter_bar)
            e.setObjectName("filter")
            e.setPlaceholderText(">100, <5" if c.type in NUMERIC else "filter")
            e.textChanged.connect(lambda t, i=i: self._col_filter(i, t))
            self.filter_edits.append(e)

    def _place_filters(self):
        hh = self.view.horizontalHeader()
        offset = self.view.frameWidth()
        for i, e in enumerate(self.filter_edits):
            if self.view.isColumnHidden(i):
                e.hide()
                continue
            x = hh.sectionViewportPosition(i) + offset
            w = hh.sectionSize(i)
            e.setGeometry(x + 1, 2, max(w - 3, 10), 24)
            e.setVisible(0 <= x + w and x < self.view.viewport().width() + offset)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        QTimer.singleShot(0, self._refit)

    def showEvent(self, ev):
        super().showEvent(ev)
        QTimer.singleShot(0, self._refit)

    def eventFilter(self, obj, ev):
        if obj is self.view.viewport() and ev.type() == QEvent.Resize:
            QTimer.singleShot(0, self._refit)
        return False

    def _refit(self):
        self._fit()
        self._place_filters()

    def _col_filter(self, i, text):
        self.proxy.set_col_filter(i, text)
        self._update_footer()

    def _search(self, text):
        self.proxy.set_search(text)
        self._update_footer()

    def clear_filters(self):
        for e in self.filter_edits:
            e.blockSignals(True)
            e.clear()
            e.blockSignals(False)
        self.proxy.col_filters = {}
        self.search.clear()
        self.proxy.invalidateFilter()
        self._update_footer()

    # ---- sorting -----------------------------------------------------
    def _header_clicked(self, col):
        shift = bool(QGuiApplication.keyboardModifiers() & Qt.ShiftModifier)
        keys = self.proxy.keys
        existing = next((k for k in keys if k[0] == col), None)
        if shift:
            if existing:
                keys[keys.index(existing)] = (col, not existing[1])
            else:
                keys.append((col, False))
        else:
            if existing and len(keys) == 1:
                keys = [(col, not existing[1])]
            else:
                keys = [(col, False)]
        self.proxy.keys = keys
        self.proxy.invalidate()
        self.proxy.sort(keys[0][0], Qt.AscendingOrder)
        self.view.horizontalHeader().setSortIndicatorShown(True)
        self.view.horizontalHeader().setSortIndicator(keys[0][0], Qt.DescendingOrder if keys[0][1] else Qt.AscendingOrder)
        names = " then ".join(f"{self.model.cols[c].label} {'↓' if d else '↑'}" for c, d in keys)
        self.view.horizontalHeader().setToolTip(f"Sorted by {names}. Shift+click adds a column.")

    # ---- data --------------------------------------------------------
    def set_rows(self, rows):
        self.model.set_rows(rows)
        if self._autosize_pending and rows:
            self.autosize()
        if self.proxy.keys:
            self.proxy.invalidate()
            self.proxy.sort(self.proxy.keys[0][0], Qt.AscendingOrder)
        self._update_footer()

    def visible_rows(self) -> list[dict]:
        out = []
        for r in range(self.proxy.rowCount()):
            src = self.proxy.mapToSource(self.proxy.index(r, 0))
            out.append(self.model.rows[src.row()])
        return out

    def visible_cols(self) -> list[Col]:
        hh = self.view.horizontalHeader()
        order = sorted(range(len(self.model.cols)), key=hh.visualIndex)
        return [self.model.cols[i] for i in order if not self.view.isColumnHidden(i)]

    def selected_rows(self) -> list[dict]:
        rows = []
        seen = set()
        for idx in self.view.selectionModel().selectedRows():
            src = self.proxy.mapToSource(idx).row()
            if src not in seen:
                seen.add(src)
                rows.append(self.model.rows[src])
        return rows

    def current_row(self) -> dict | None:
        idx = self.view.currentIndex()
        if not idx.isValid():
            sel = self.selected_rows()
            return sel[0] if sel else None
        return self.model.rows[self.proxy.mapToSource(idx).row()]

    def _double(self, idx):
        col = self.model.cols[self.proxy.mapToSource(idx).column()]
        if col.editable and self.model.on_edit:
            return
        self.activated.emit(self.model.rows[self.proxy.mapToSource(idx).row()])

    def _update_footer(self, *args):
        total = len(self.model.rows)
        # the count and the sums do not depend on the order: with no filter every row is visible
        rows = self.model.rows if self.proxy.rowCount() == total else self.visible_rows()
        self.count.setText(f"{len(rows):,} of {total:,} rows" if len(rows) != total else f"{total:,} rows")
        sums = []
        for c in (self.visible_cols() if self.show_totals else []):
            from ...core.reports import NO_TOTAL_KEYS
            auto = c.type == "money" and not c.editable and c.key not in NO_TOTAL_KEYS
            if c.total if c.total is not None else auto:
                s = sum((r.get(c.key) or 0) for r in rows if r.get("style") is None and isinstance(r.get(c.key), (int, float)))
                sums.append(f"{c.label}: {fmt_money(s)}")
        self.totals.setText("   ·   ".join(sums[:5]))

    def copy_selection(self):
        idxs = self.view.selectionModel().selectedIndexes()
        if not idxs:
            return
        rows = sorted({i.row() for i in idxs})
        cols = [i for i in sorted({i.column() for i in idxs}) if not self.view.isColumnHidden(i)]
        lines = ["\t".join(self.model.cols[c].label for c in cols)]
        for r in rows:
            lines.append("\t".join(str(self.proxy.index(r, c).data(Qt.DisplayRole) or "") for c in cols))
        QGuiApplication.clipboard().setText("\n".join(lines))

    def export_dialog(self, title: str | None = None, company: str = ""):
        from datetime import datetime
        title = title or self.export_title or "Export"
        safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in title).strip()
        path, _ = QFileDialog.getSaveFileName(self, "Export to Excel", f"{safe} {datetime.now():%Y-%m-%d}.xlsx",
                                              "Excel workbook (*.xlsx)")
        if not path:
            return None
        return self.export_to(path, title, company)

    def export_pdf_dialog(self, title: str | None = None, company: str = "", subtitle: str = "", notes: str = ""):
        from datetime import datetime
        title = title or self.export_title or "Report"
        safe = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in title).strip()
        path, _ = QFileDialog.getSaveFileName(self, "Save as PDF", f"{safe} {datetime.now():%Y-%m-%d}.pdf",
                                              "PDF (*.pdf)")
        if not path:
            return None
        from ..printing import table_pdf
        cols = [(c.key, c.label, c.type) for c in self.visible_cols()]
        rows = [{c.key: display(c, r.get(c.key)) if c.type != "bool" else ("Yes" if r.get(c.key) else "No")
                 for c in self.visible_cols()} | {"style": r.get("style")} for r in self.visible_rows()]
        totals = {}
        for c in (self.visible_cols() if self.show_totals else []):
            from ...core.reports import NO_TOTAL_KEYS
            if c.type in ("money", "qty", "int", "hours") and c.key not in NO_TOTAL_KEYS and any(r.get("style") is None for r in self.visible_rows()):
                vals = [r.get(c.key) for r in self.visible_rows() if r.get("style") is None and isinstance(r.get(c.key), (int, float))]
                if vals and c.type == "money":
                    totals[c.key] = fmt_money(sum(vals))
                elif vals:
                    totals[c.key] = display(c, sum(vals))
        return table_pdf(path, title, subtitle, cols, rows, totals, company, notes)

    def export_to(self, path: str, title: str, company: str = "", params: dict | None = None, notes: str = ""):
        from ...core.excel import export_table
        cols = [(c.key, c.label, c.type) for c in self.visible_cols()]
        rows = []
        for r in self.visible_rows():
            out = dict(r)
            for c in self.visible_cols():
                if c.choices is not None:
                    out[c.key] = display(c, r.get(c.key))
                elif c.type == "bool":
                    out[c.key] = "Yes" if r.get(c.key) else "No"
            rows.append(out)
        cols = [(k, l, "text" if t == "bool" else t) for k, l, t in cols]
        return export_table(path, title, cols, rows, totals=self.show_totals and any(t == "money" for _, _, t in cols),
                            company=company, params=params, notes=notes)
