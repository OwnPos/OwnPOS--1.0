"""This station's connection to the store server: server address, database, login, station number.

The database password is stored encrypted with Windows DPAPI (CryptProtectData), which ties it to this
Windows user on this computer: copying connection.json to another PC does not reveal or reuse the password.
On other systems (tests, development) the password is kept base64-encoded and marked as not protected."""
from __future__ import annotations

import base64
import json
import sys
from dataclasses import asdict, dataclass

from .paths import connection_file

DEFAULT_PORT = 5432
DEFAULT_DB = "ownpos_store"
DEFAULT_USER = "ownpos"


@dataclass
class Connection:
    host: str = "localhost"
    port: int = DEFAULT_PORT
    dbname: str = DEFAULT_DB
    user: str = DEFAULT_USER
    password: str = ""
    station_id: int = 1
    station_name: str = ""
    timezone: str = "America/Chicago"

    def conninfo(self, dbname: str | None = None) -> dict:
        return {"host": self.host, "port": int(self.port), "dbname": dbname or self.dbname, "user": self.user,
                "password": self.password}


# ---- Windows DPAPI ------------------------------------------------------------------------------------
def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    entropy_raw = b"OwnPOS station connection"
    ebuf = ctypes.create_string_buffer(entropy_raw, len(entropy_raw))
    entropy = BLOB(len(entropy_raw), ctypes.cast(ebuf, ctypes.POINTER(ctypes.c_char)))
    crypt32 = ctypes.windll.crypt32
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    ok = (fn(ctypes.byref(blob_in), "OwnPOS", ctypes.byref(entropy), None, None, 0, ctypes.byref(blob_out))
          if protect else
          fn(ctypes.byref(blob_in), None, ctypes.byref(entropy), None, None, 0, ctypes.byref(blob_out)))
    if not ok:
        raise OSError("Windows could not " + ("protect" if protect else "read") + " the saved password")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def protect(secret: str) -> dict:
    raw = secret.encode("utf-8")
    if sys.platform == "win32":
        return {"dpapi": base64.b64encode(_dpapi(raw, True)).decode("ascii")}
    return {"plain_b64": base64.b64encode(raw).decode("ascii")}


def unprotect(stored: dict | None) -> str:
    if not stored:
        return ""
    if "dpapi" in stored:
        if sys.platform != "win32":
            raise OSError("This connection file was made on Windows and only opens there")
        return _dpapi(base64.b64decode(stored["dpapi"]), False).decode("utf-8")
    return base64.b64decode(stored.get("plain_b64", "")).decode("utf-8")


# ---- file ---------------------------------------------------------------------------------------------
def load() -> Connection | None:
    f = connection_file()
    if not f.exists():
        return None
    data = json.loads(f.read_text(encoding="utf-8"))
    pw = unprotect(data.pop("password_protected", None))
    data.pop("password", None)
    known = set(Connection.__dataclass_fields__)
    return Connection(password=pw, **{k: v for k, v in data.items() if k in known})


def save(c: Connection) -> None:
    data = asdict(c)
    data.pop("password")
    data["password_protected"] = protect(c.password)
    f = connection_file()
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(f)
