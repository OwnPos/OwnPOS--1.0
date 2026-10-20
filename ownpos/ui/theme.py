"""Colours, fonts and the Qt stylesheet, in a light and a dark version.

How it works:
- LIGHT and DARK are palettes: the same names (bg, panel, ink, crimson = the primary action colour...) with
  different colours.
- build_stylesheet(p) writes the whole Qt stylesheet from one palette, so every screen follows it.
- apply_theme(app, mode) picks the palette ("light", "dark" or "system" = follow Windows) and applies it.
- pal() gives the current palette to code that paints by hand (charts, tiles, coloured rows).

OwnPOS colours come from its logo: deep blue, teal and a touch of amber. (The key "crimson" is the name of the
primary-button colour in this palette; it is teal in OwnPOS.)"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor, QPalette

# the white tick drawn inside ticked boxes (forward slashes: Qt style sheets need them, also on Windows)
CHECK_IMAGE = (Path(__file__).resolve().parent / "assets" / "check.png").as_posix()

LIGHT = {
    "bg": "#F4F5F7", "panel": "#FFFFFF", "field": "#FFFFFF", "field_border": "#CDD2D9",
    "line": "#E3E6EA", "grid": "#EEF0F3", "alt_row": "#FAFBFC", "header": "#F7F8FA", "header_ink": "#344054",
    "ink": "#1D2433", "muted": "#667085", "disabled_ink": "#A0A6B0",
    "hover": "#F0F2F5", "pressed": "#E4E7EC", "select": "#DCE6FA",
    "crimson": "#0F7A86", "crimson_dark": "#0B5E68", "primary_disabled": "#9CC9CE",
    "accent": "#14507A", "accent_dark": "#0E3D5E", "accent_disabled": "#A6BDD0", "accent_line": "#14507A",
    "danger_border": "#F1B5AE",
    "sidebar": "#0E2740", "sidebar_hover": "#173A5C", "sidebar_ink": "#D6E0EA", "sidebar_muted": "#7E93A8",
    "brand_sub": "#9DB0C3", "side_scroll": "#2C4A68",
    "good": "#1E7F4F", "warn": "#B26A00", "bad": "#B42318", "info": "#14507A",
    "tooltip_bg": "#1D2433", "tooltip_ink": "#FFFFFF",
    "chart_bar": "#14507A", "chart_highlight": "#E0A22A", "chart_text": "#344054", "chart_axis": "#667085",
    "chart_grid": "#EEF0F3", "tile_border": "#DDE1E6", "tile_hover": "#F5F7FA",
}

DARK = {
    "bg": "#12161D", "panel": "#1A2029", "field": "#202733", "field_border": "#364152",
    "line": "#2B3340", "grid": "#262E3A", "alt_row": "#171C24", "header": "#1E2530", "header_ink": "#C7CDD6",
    "ink": "#E6E9EE", "muted": "#98A2B3", "disabled_ink": "#5F6B7A",
    "hover": "#252D39", "pressed": "#2D3645", "select": "#2B3E66",
    "crimson": "#1A9AA8", "crimson_dark": "#15808C", "primary_disabled": "#1F4A50",
    "accent": "#2E79B0", "accent_dark": "#266594", "accent_disabled": "#26405A", "accent_line": "#7DB6E0",
    "danger_border": "#6B2C2C",
    "sidebar": "#0A1726", "sidebar_hover": "#15283D", "sidebar_ink": "#C7D3DF", "sidebar_muted": "#687D92",
    "brand_sub": "#8A94A3", "side_scroll": "#2A3344",
    "good": "#4CC38A", "warn": "#F0B44C", "bad": "#F2706A", "info": "#7FA2F0",
    "tooltip_bg": "#E6E9EE", "tooltip_ink": "#12161D",
    "chart_bar": "#4A9AD4", "chart_highlight": "#F0B44C", "chart_text": "#C7CDD6", "chart_axis": "#8A94A3",
    "chart_grid": "#2B3340", "tile_border": "#323B49", "tile_hover": "#232B37",
}

FONT_FAMILY = "Segoe UI, Noto Sans, Arial"

_current = LIGHT
_mode = "light"


def pal() -> dict:
    """The palette in use right now."""
    return _current


def is_dark() -> bool:
    return _current is DARK


def row_bg(color: str) -> str:
    """A highlight colour for a table row. Light tints (pale red, pale green) become deep tints in dark mode,
    so light text stays readable on them."""
    if not is_dark():
        return color
    c = QColor(color)
    h, s, _l, _a = c.getHslF()
    return QColor.fromHslF(max(h, 0.0), min(s, 0.42), 0.21).name()


def build_stylesheet(p: dict) -> str:
    return f"""
* {{ font-family: {FONT_FAMILY}; font-size: 10pt; color: {p['ink']}; }}
QMainWindow, QDialog {{ background: {p['bg']}; }}
QWidget#page {{ background: {p['bg']}; }}
QFrame#panel {{ background: {p['panel']}; border: 1px solid {p['line']}; border-radius: 8px; }}
QLabel#pageTitle {{ font-size: 16pt; font-weight: 600; }}
QLabel#pageSub {{ color: {p['muted']}; }}
QLabel#sectionTitle {{ font-size: 11pt; font-weight: 600; }}
QLabel#muted {{ color: {p['muted']}; }}
QLabel#notice {{ color: {p['warn']}; font-weight: 600; }}
QLabel#kpiValue {{ font-size: 17pt; font-weight: 600; }}
QLabel#kpiLabel {{ color: {p['muted']}; font-size: 9pt; }}
QLabel#bigTotal {{ font-size: 26pt; font-weight: 700; color: {p['ink']}; }}

/* sidebar */
QFrame#sidebar {{ background: {p['sidebar']}; }}
QLabel#brand {{ color: white; font-size: 12pt; font-weight: 700; }}
QLabel#brandSub {{ color: {p['brand_sub']}; font-size: 8.5pt; }}
QPushButton#nav {{ background: transparent; color: {p['sidebar_ink']}; border: none; text-align: left; padding: 9px 14px;
    border-radius: 6px; font-size: 10.5pt; }}
QPushButton#nav:hover {{ background: {p['sidebar_hover']}; color: white; }}
QPushButton#nav:checked {{ background: {p['crimson']}; color: white; font-weight: 600; }}
QLabel#navGroup {{ color: {p['sidebar_muted']}; font-size: 8pt; font-weight: 600; padding: 10px 14px 2px 14px; }}
QFrame#sidebar[compact="true"] QPushButton#nav {{ padding: 4px 14px; }}
QFrame#sidebar[compact="true"] QLabel#navGroup {{ padding: 6px 14px 1px 14px; }}
QFrame#sidebar[compact="tight"] QPushButton#nav {{ padding: 3px 14px; font-size: 10pt; }}
QFrame#sidebar[compact="tight"] QLabel#navGroup {{ padding: 4px 14px 0px 14px; font-size: 7.5pt; }}
QScrollArea#sideScroll {{ background: {p['sidebar']}; }}
QScrollArea#sideScroll QScrollBar:vertical {{ background: {p['sidebar']}; width: 6px; border: none; }}
QScrollArea#sideScroll QScrollBar::handle:vertical {{ background: {p['side_scroll']}; border-radius: 3px; min-height: 30px; }}
QScrollArea#sideScroll QScrollBar::add-line:vertical, QScrollArea#sideScroll QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollArea#pageScroll > QWidget > QWidget#page {{ background: {p['bg']}; }}

/* buttons */
QPushButton {{ background: {p['panel']}; border: 1px solid {p['field_border']}; border-radius: 6px; padding: 6px 12px; }}
QPushButton:hover {{ background: {p['hover']}; }}
QPushButton:pressed {{ background: {p['pressed']}; }}
QPushButton:checked {{ background: {p['accent']}; color: white; border-color: {p['accent']}; }}
QPushButton:disabled {{ color: {p['disabled_ink']}; background: {p['bg']}; }}
QPushButton#primary {{ background: {p['crimson']}; color: white; border: 1px solid {p['crimson']}; font-weight: 600; }}
QPushButton#primary:hover {{ background: {p['crimson_dark']}; }}
QPushButton#primary:disabled {{ background: {p['primary_disabled']}; border-color: {p['primary_disabled']}; }}
QPushButton#blue {{ background: {p['accent']}; color: white; border: 1px solid {p['accent']}; font-weight: 600; }}
QPushButton#blue:hover {{ background: {p['accent_dark']}; }}
QPushButton#blue:disabled {{ background: {p['accent_disabled']}; border-color: {p['accent_disabled']}; }}
QPushButton#danger {{ color: {p['bad']}; border-color: {p['danger_border']}; }}
QPushButton#pay {{ background: {p['crimson']}; color: white; border: none; font-size: 16pt; font-weight: 700;
    border-radius: 8px; padding: 14px; }}
QPushButton#pay:hover {{ background: {p['crimson_dark']}; }}
QPushButton#tile {{ border-radius: 8px; padding: 6px; font-size: 9.5pt; text-align: center; }}
QPushButton#tender {{ font-size: 12pt; padding: 12px; border-radius: 8px; }}
QPushButton#tender:checked {{ background: {p['accent']}; color: white; border-color: {p['accent']}; }}
QPushButton#key {{ font-size: 14pt; padding: 10px; border-radius: 8px; min-width: 56px; }}
QPushButton#link {{ border: none; background: transparent; color: {p['accent_line']}; text-decoration: underline; padding: 2px; }}

/* inputs */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit, QTextEdit, QPlainTextEdit, QTextBrowser {{
    background: {p['field']}; border: 1px solid {p['field_border']}; border-radius: 6px; padding: 5px 7px;
    selection-background-color: {p['accent']}; selection-color: white; }}
QLineEdit:focus, QComboBox:focus, QDateEdit:focus, QTextEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {p['accent_line']}; }}
QComboBox QAbstractItemView {{ background: {p['panel']}; border: 1px solid {p['line']};
    selection-background-color: {p['select']}; selection-color: {p['ink']}; }}
QLineEdit#scan {{ font-size: 14pt; padding: 9px; border: 2px solid {p['accent_line']}; }}
QLineEdit#filter {{ padding: 2px 4px; border-radius: 4px; font-size: 9pt; }}
QLineEdit#amount {{ font-size: 18pt; padding: 8px; font-weight: 600; }}

/* tables */
QTableView, QTableWidget, QTreeWidget, QListWidget {{ background: {p['panel']}; border: 1px solid {p['line']};
    border-radius: 6px; gridline-color: {p['grid']}; selection-background-color: {p['select']}; selection-color: {p['ink']};
    alternate-background-color: {p['alt_row']}; }}
QHeaderView::section {{ background: {p['header']}; border: none; border-bottom: 1px solid {p['line']};
    border-right: 1px solid {p['grid']}; padding: 6px; font-weight: 600; color: {p['header_ink']}; }}
QTableCornerButton::section {{ background: {p['header']}; border: none; }}
QTableView#cart {{ font-size: 11.5pt; }}

QTabWidget::pane {{ border: 1px solid {p['line']}; border-radius: 6px; background: {p['panel']}; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 8px 14px; border: none; color: {p['muted']}; }}
QTabBar::tab:selected {{ color: {p['ink']}; border-bottom: 2px solid {p['crimson']}; font-weight: 600; }}
QCheckBox::indicator, QTableView::indicator {{ width: 15px; height: 15px; border: 1px solid {p['field_border']};
    border-radius: 3px; background: {p['field']}; }}
QCheckBox::indicator:checked, QTableView::indicator:checked {{ background: {p['accent']}; border-color: {p['accent']};
    image: url({CHECK_IMAGE}); }}
QCheckBox::indicator:disabled {{ background: {p['bg']}; border-color: {p['line']}; }}
QCheckBox::indicator:checked:disabled {{ background: {p['accent_disabled']}; border-color: {p['accent_disabled']}; }}
QGroupBox {{ background: {p['panel']}; border: 1px solid {p['line']}; border-radius: 8px; margin-top: 14px; padding: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; font-weight: 600; }}
QStatusBar {{ background: {p['panel']}; border-top: 1px solid {p['line']}; color: {p['muted']}; }}
QScrollArea {{ border: none; background: transparent; }}
QMenu {{ background: {p['panel']}; border: 1px solid {p['line']}; }}
QMenu::item:selected {{ background: {p['select']}; }}
QToolTip {{ background: {p['tooltip_bg']}; color: {p['tooltip_ink']}; border: none; padding: 5px; }}
"""


# kept for older imports: the light stylesheet
STYLESHEET = build_stylesheet(LIGHT)


def _qt_palette(p: dict) -> QPalette:
    """Native parts Qt draws itself (message boxes, calendar pop-ups, scroll bars) follow this."""
    q = QPalette()
    q.setColor(QPalette.Window, QColor(p["bg"]))
    q.setColor(QPalette.WindowText, QColor(p["ink"]))
    q.setColor(QPalette.Base, QColor(p["field"]))
    q.setColor(QPalette.AlternateBase, QColor(p["alt_row"]))
    q.setColor(QPalette.Text, QColor(p["ink"]))
    q.setColor(QPalette.Button, QColor(p["panel"]))
    q.setColor(QPalette.ButtonText, QColor(p["ink"]))
    q.setColor(QPalette.Highlight, QColor(p["accent"]))
    q.setColor(QPalette.HighlightedText, QColor("#FFFFFF"))
    q.setColor(QPalette.ToolTipBase, QColor(p["tooltip_bg"]))
    q.setColor(QPalette.ToolTipText, QColor(p["tooltip_ink"]))
    q.setColor(QPalette.PlaceholderText, QColor(p["muted"]))
    q.setColor(QPalette.Link, QColor(p["accent_line"]))
    q.setColor(QPalette.Disabled, QPalette.Text, QColor(p["disabled_ink"]))
    q.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(p["disabled_ink"]))
    return q


def system_is_dark(app) -> bool:
    """True when Windows is set to dark mode (Qt 6.5 or newer can tell)."""
    try:
        from PySide6.QtCore import Qt
        return app.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:  # noqa: BLE001 - older Qt: assume light
        return False


def apply_theme(app, mode: str = "system") -> None:
    """mode: 'light', 'dark' or 'system' (follow Windows). Restyles the whole app."""
    global _current, _mode
    _mode = mode if mode in ("light", "dark", "system") else "system"
    dark = _mode == "dark" or (_mode == "system" and system_is_dark(app))
    _current = DARK if dark else LIGHT
    app.setStyle("Fusion")            # the same look on every Windows version, and it honours the palette
    app.setPalette(_qt_palette(_current))
    app.setStyleSheet(build_stylesheet(_current))


def current_mode() -> str:
    return _mode
