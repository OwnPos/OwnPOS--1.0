"""Where OwnPOS keeps its own files on each computer (never the store's data - that is on the server).

- Installed program:   %LOCALAPPDATA%\\OwnPOS\\        (connection file, logs, report workbooks, backups made here)
- Running from source: %LOCALAPPDATA%\\OwnPOS-Dev\\    (so testing never touches a real station's settings)
- OWNPOS_DATA environment variable overrides both (used by tests and the self-test).

Pasal POS uses different folders (PasalPOS, PasalPOS-Dev), so the two programs never share files."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def data_root() -> Path:
    env = os.environ.get("OWNPOS_DATA")
    if env:
        p = Path(env)
    else:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
        p = Path(base) / ("OwnPOS" if getattr(sys, "frozen", False) else "OwnPOS-Dev")
    p.mkdir(parents=True, exist_ok=True)
    return p


def connection_file() -> Path:
    return data_root() / "connection.json"


def log_folder() -> Path:
    p = data_root() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p
