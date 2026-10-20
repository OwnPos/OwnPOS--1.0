"""Product branding in one place: the OwnPOS icon and the Windows taskbar identity.

The mark is an "O" that is also a padlock (your own business, kept secure) with rising bars inside it
(managing it yourself), white on a deep blue tile. Source drawing: assets/ownpos.svg, rendered to the PNG
and .ico files in assets/."""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QIcon, QPixmap

ASSETS = Path(__file__).resolve().parent / "assets"

# Only for small per-computer preferences such as column widths. Pasal POS uses a different name, so the two
# programs never share these preferences either.
ORG_NAME = "OwnPOS"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def ui_settings() -> QSettings:
    """Small per-computer preferences (column widths, hidden columns). Not store data: that is on the server."""
    return QSettings(ORG_NAME, "OwnPOS")


def app_icon() -> QIcon:
    """One QIcon holding every size, so Windows picks the sharpest image for the title bar, taskbar and Alt+Tab."""
    icon = QIcon()
    for s in ICON_SIZES:
        f = ASSETS / f"ownpos-{s}.png"
        if f.exists():
            icon.addFile(str(f))
    return icon


def logo_pixmap(size: int) -> QPixmap:
    return app_icon().pixmap(size, size)


def set_windows_app_id() -> None:
    """Without this, Windows groups the app under python.exe and shows Python's icon on the taskbar."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("OwnPOS.PointOfSale")
        except Exception:  # noqa: BLE001 - cosmetic only, never stop the app
            pass
