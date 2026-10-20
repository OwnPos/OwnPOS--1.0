# PyInstaller recipe for OwnPOS (one-folder build: fast start, easy updates).
#   pyinstaller --noconfirm --clean ownpos.spec
# Output: dist/OwnPOS/OwnPOS.exe  (installer/OwnPOS.iss packs this folder into one setup file)
# The SAME program is installed on the server PC and on every station; the first screen asks which it is.
import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("ownpos/core/schema.sql", "ownpos/core"), ("ownpos/docs", "ownpos/docs"),
         ("ownpos/ui/assets", "ownpos/ui/assets")]
binaries = []
# every screen and service is listed, so a screen opened from a menu can never be missing from the build
hiddenimports = collect_submodules("ownpos")
for pkg in ("psycopg", "cryptography", "openpyxl", "xlrd"):     # psycopg_binary: by the psycopg hook
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h
try:                                     # optional AI fallback of Ask OwnPOS
    d, b, h = collect_all("anthropic")
    datas += d
    binaries += b
    hiddenimports += h
except Exception:  # noqa: BLE001
    pass
if sys.platform == "win32":
    hiddenimports += ["win32print", "win32api", "serial", "serial.tools.list_ports"]

icon = "ownpos/ui/assets/ownpos.ico"     # one icon file: window, .exe and installer

a = Analysis(["launcher.py"], pathex=["."], binaries=binaries, datas=datas, hiddenimports=hiddenimports,
             excludes=["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.Qt3DCore",
                       "PySide6.QtQuick3D", "PySide6.QtMultimedia", "PySide6.QtCharts", "PySide6.QtDataVisualization",
                       "devpos"],
             noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="OwnPOS", console=False, icon=icon,
          version="installer/version_info.txt" if os.path.exists("installer/version_info.txt") else None)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="OwnPOS")
