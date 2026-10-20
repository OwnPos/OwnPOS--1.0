"""Cash drawer and customer display.

Both optional: if a device or driver library is missing the program keeps working and the cashier opens the
drawer with its key.

- Cash drawer: an ESC/POS "kick" sent to the receipt printer (the drawer cable plugs into the printer).
  Uses pywin32 on Windows to send raw bytes, or a serial port.
- Customer display: a 2x20 serial pole display.
"""
from __future__ import annotations

import sys

DRAWER_KICK = b"\x1b\x70\x00\x19\xfa"   # ESC p 0 25 250


def open_drawer(printer_name: str = "", port: str = "") -> tuple[bool, str]:
    if port:
        try:
            import serial
            with serial.Serial(port, 9600, timeout=1) as ser:
                ser.write(DRAWER_KICK)
            return True, "Drawer opened"
        except Exception as exc:  # noqa: BLE001
            return False, f"Could not open drawer on {port}: {exc}"
    if sys.platform.startswith("win"):
        try:
            import win32print
            name = printer_name or win32print.GetDefaultPrinter()
            h = win32print.OpenPrinter(name)
            try:
                win32print.StartDocPrinter(h, 1, ("Drawer", None, "RAW"))
                win32print.StartPagePrinter(h)
                win32print.WritePrinter(h, DRAWER_KICK)
                win32print.EndPagePrinter(h)
                win32print.EndDocPrinter(h)
            finally:
                win32print.ClosePrinter(h)
            return True, "Drawer opened"
        except ImportError:
            return False, "Install pywin32 to open the drawer through the receipt printer"
        except Exception as exc:  # noqa: BLE001
            return False, f"Could not open drawer: {exc}"
    return False, "No cash drawer configured"


def show_on_display(port: str, line1: str, line2: str = "") -> None:
    if not port:
        return
    try:
        import serial
        with serial.Serial(port, 9600, timeout=1) as ser:
            ser.write(b"\x0c" + line1[:20].ljust(20).encode("ascii", "replace") + line2[:20].ljust(20).encode("ascii", "replace"))
    except Exception:  # noqa: BLE001
        pass
