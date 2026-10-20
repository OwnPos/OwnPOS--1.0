"""Writes installer/version.iss from ownpos.APP_VERSION and prints the version.

build_exe.bat runs this before Inno Setup, so the installer is always named OwnPOS-Setup-<version>.exe and
Windows Settings > Apps shows the right version."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ownpos import APP_VERSION  # noqa: E402

TEXT = ('; written by tools/write_version.py from ownpos/__init__.py - do not edit by hand\r\n'
        '#ifndef MyAppVersion\r\n'
        f'  #define MyAppVersion "{APP_VERSION}"\r\n'
        '#endif\r\n')

if __name__ == "__main__":
    target = ROOT / "installer" / "version.iss"
    if not target.exists() or target.read_bytes() != TEXT.encode():
        target.write_bytes(TEXT.encode())
    print(APP_VERSION)
