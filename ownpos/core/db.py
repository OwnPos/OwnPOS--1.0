"""Database access: the store's PostgreSQL database on the server PC, shared by every station.

Safety rules (see docs/DESIGN.md, section 3.1):

* Every multi-step operation (a sale, a receiving, a Z-Out) runs inside `db.tx()`: all of it is saved or
  none of it is.
* Every writing transaction first takes ONE store-wide write lock (a PostgreSQL advisory lock). Writes from
  the four stations therefore happen one after another, exactly like the single-computer program this code
  was first written and tested as. A sale holds the lock for a few milliseconds. Reading never waits.
* A single write statement outside `tx()` is wrapped in its own locked transaction automatically.
* The store's clock is the server's clock (`now_str()`), so stations can never disagree about dates.
* If the server cannot be reached, `ServerUnavailable` is raised. If the connection broke while a
  transaction was being committed, `CommitUnknown` is raised: the caller must check whether the work was
  saved (sales do this with their sale key) before trying again.
"""
from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.adapt import Loader

# Bump SCHEMA_VERSION and add a function to MIGRATIONS whenever an existing table gets a new column.
# New tables need no migration: schema.sql (CREATE ... IF NOT EXISTS) runs whenever the program version changes.
SCHEMA_VERSION = 1
SCHEMA_FILE = Path(__file__).with_name("schema.sql")
WRITE_LOCK_KEY = 7_042_001          # the store-wide write lock (any fixed number, same on every station)
CLOCK_WARN_SECONDS = 120


class ServerUnavailable(Exception):
    """The server (or the network to it) is not reachable."""


class CommitUnknown(ServerUnavailable):
    """The connection broke while saving: the work may or may not have been saved."""


# ---- the store clock ------------------------------------------------------------------------------------
_clock = {"offset": 0.0, "checked": 0.0}


def now_dt() -> datetime:
    return datetime.now() + timedelta(seconds=_clock["offset"])


def now_str() -> str:
    """Store date and time from the server clock, 'YYYY-MM-DD HH:MM:SS'."""
    return now_dt().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return now_dt().strftime("%Y-%m-%d")


def clock_offset_seconds() -> float:
    """How far this computer's clock is from the server's (positive = this PC is behind)."""
    return _clock["offset"]


# ---- rows ---------------------------------------------------------------------------------------------
class Row:
    """A result row that works like sqlite3.Row: row['name'], row[0], unpacking, dict(row)."""
    __slots__ = ("_values", "_index")

    def __init__(self, index: dict, values):
        self._index = index
        self._values = tuple(values)

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._values[key]
        try:
            return self._values[self._index[key]]
        except KeyError:
            low = key.lower()
            if low in self._index:              # PostgreSQL folds unquoted names to lower case
                return self._values[self._index[low]]
            raise KeyError(key) from None

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self):
        return list(self._index)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return f"Row({dict(zip(self._index, self._values))!r})"


def _row_factory(cursor):
    names = [c.name for c in (cursor.description or [])]
    index = {n: i for i, n in enumerate(names)}

    def make(values):
        return Row(index, values)
    return make


class _NumericLoader(Loader):
    """NUMERIC results (from SUM of BIGINT, AVG, ROUND) as int when whole, else float, so they mix with
    the program's ints and floats exactly like SQLite's results did."""

    def load(self, data):
        d = Decimal(bytes(data).decode())
        if d == d.to_integral_value():
            return int(d)
        return float(d)


def _translate(sql: str) -> str:
    """'?' placeholders (the program's style) -> psycopg '%s'; a literal % must be doubled."""
    return sql.replace("%", "%%").replace("?", "%s")


_WRITE_WORDS = ("insert", "update", "delete", "create", "alter", "drop", "truncate", "comment", "grant")


def _is_write(sql: str) -> bool:
    return sql.lstrip().split(None, 1)[0].lower() in _WRITE_WORDS if sql.strip() else False


class Database:
    """One connection to the store database. Thread-safe for this program's use (one lock per connection)."""

    def __init__(self, conninfo: dict, read_only: bool = False, station_id: int | None = None,
                 timezone: str = "America/Chicago"):
        self.conninfo = dict(conninfo)
        self.read_only = read_only
        self.station_id = station_id
        self.timezone = timezone
        self.name = self.conninfo.get("dbname", "")
        self.path = self.name                       # kept for code that shows "where the data is"
        self.conn: psycopg.Connection | None = None
        self._depth = 0
        self._lock = threading.RLock()
        self._connect()

    # ---- connection ----------------------------------------------------------------------------
    def _connect(self) -> None:
        params = {k: v for k, v in self.conninfo.items() if v not in (None, "")}
        params.setdefault("connect_timeout", 5)
        params.setdefault("application_name", f"OwnPOS station {self.station_id or '-'}")
        try:
            conn = psycopg.connect(autocommit=True, row_factory=_row_factory, **params)
        except psycopg.OperationalError as e:
            raise ServerUnavailable(_plain_error(e)) from e
        conn.adapters.register_loader("numeric", _NumericLoader)
        with conn.cursor() as cur:
            cur.execute("SELECT set_config('TimeZone', %s, false)", (self.timezone,))
            if self.read_only:
                cur.execute("SET default_transaction_read_only = on")
        self.conn = conn
        self._depth = 0
        self.sync_clock()

    def reconnect(self) -> None:
        with self._lock:
            try:
                if self.conn is not None:
                    self.conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._connect()

    def is_alive(self) -> bool:
        try:
            with self._lock:
                self.conn.execute("SELECT 1")
            return True
        except Exception:  # noqa: BLE001
            return False

    def sync_clock(self) -> float:
        """Read the server clock and remember how far this PC's clock is from it."""
        with self._lock:
            before = time.time()
            cur = self.conn.execute("SELECT to_char(localtimestamp, 'YYYY-MM-DD HH24:MI:SS.US')")
            server = datetime.strptime(cur.fetchone()[0], "%Y-%m-%d %H:%M:%S.%f")
            after = time.time()
        local_mid = datetime.fromtimestamp((before + after) / 2)
        _clock["offset"] = (server - local_mid).total_seconds()
        _clock["checked"] = after
        return _clock["offset"]

    # ---- low level -----------------------------------------------------------------------------
    def _run(self, sql: str, params=()):
        try:
            return self.conn.execute(_translate(sql), tuple(params))
        except psycopg.OperationalError as e:
            self._broken(e)
            raise ServerUnavailable(_plain_error(e)) from e
        except psycopg.InterfaceError as e:          # connection already closed
            self._broken(e)
            raise ServerUnavailable("The connection to the server was closed") from e

    def _broken(self, _e) -> None:
        self._depth = 0

    def execute_script(self, sql: str) -> None:
        """Run a whole SQL file as it is (no placeholders)."""
        with self._lock:
            try:
                self.conn.execute(sql)
            except psycopg.OperationalError as e:
                raise ServerUnavailable(_plain_error(e)) from e

    # ---- transactions --------------------------------------------------------------------------
    @contextmanager
    def tx(self):
        """Nested-safe transaction holding the store-wide write lock. Only the outermost level commits."""
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._run("BEGIN")
                if not self.read_only:
                    self._run("SELECT pg_advisory_xact_lock(?)", (WRITE_LOCK_KEY,))
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if outer:
                    try:
                        self.conn.execute("ROLLBACK")
                    except Exception:  # noqa: BLE001 - connection may be gone; the server rolls back itself
                        pass
                raise
            else:
                self._depth -= 1
                if outer:
                    try:
                        self.conn.execute("COMMIT")
                    except (psycopg.OperationalError, psycopg.InterfaceError) as e:
                        self._broken(e)
                        raise CommitUnknown("The connection broke while saving. Check whether it was saved "
                                            "before trying again.") from e

    @property
    def in_tx(self) -> bool:
        return self._depth > 0

    # ---- query helpers -------------------------------------------------------------------------
    def exec(self, sql: str, params=()):
        with self._lock:
            if self._depth == 0 and _is_write(sql) and not self.read_only:
                with self.tx():
                    return self._run(sql, params)
            return self._run(sql, params)

    def executemany(self, sql: str, rows) -> None:
        with self._lock:
            with self.tx():
                try:
                    with self.conn.cursor() as cur:
                        cur.executemany(_translate(sql), [tuple(r) for r in rows])
                except psycopg.OperationalError as e:
                    raise ServerUnavailable(_plain_error(e)) from e

    def insert(self, sql: str, params=()) -> int:
        """INSERT ... and return the new row's id."""
        if " returning " not in sql.lower():
            sql = sql.rstrip().rstrip(";") + " RETURNING id"
        with self._lock:
            return self.exec(sql, params).fetchone()[0]

    def all(self, sql: str, params=()) -> list[Row]:
        with self._lock:
            return self._run(sql, params).fetchall()

    def one(self, sql: str, params=()) -> Row | None:
        with self._lock:
            return self._run(sql, params).fetchone()

    def val(self, sql: str, params=(), default=None):
        row = self.one(sql, params)
        if row is None or row[0] is None:
            return default
        return row[0]

    def dicts(self, sql: str, params=()) -> list[dict]:
        rows = self.all(sql, params)
        if not rows:
            return []
        names = list(rows[0]._index)            # same columns for every row: zip is much faster than dict(row)
        return [dict(zip(names, r._values)) for r in rows]

    # ---- settings ------------------------------------------------------------------------------
    def get_setting(self, key: str, default=None):
        row = self.one("SELECT value FROM settings WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            return json.loads(row[0])
        except (TypeError, ValueError):
            return row[0]

    def set_setting(self, key: str, value) -> None:
        self.exec("INSERT INTO settings(key, value) VALUES(?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                  (key, json.dumps(value, ensure_ascii=False)))

    def settings(self) -> dict:
        out = {}
        for r in self.all("SELECT key, value FROM settings"):
            try:
                out[r["key"]] = json.loads(r["value"])
            except (TypeError, ValueError):
                out[r["key"]] = r["value"]
        return out

    # ---- sequences -----------------------------------------------------------------------------
    def next_seq(self, name: str) -> int:
        """Gap-free counter; call inside a transaction (the write lock makes it safe across stations)."""
        if not self.in_tx:
            raise RuntimeError("next_seq must be called inside a transaction")
        self.exec("INSERT INTO sequences(name, last) VALUES(?, 0) ON CONFLICT(name) DO NOTHING", (name,))
        return int(self.val("UPDATE sequences SET last = last + 1 WHERE name = ? RETURNING last", (name,)))

    def peek_seq(self, name: str) -> int:
        return int(self.val("SELECT last FROM sequences WHERE name = ?", (name,), 0)) + 1

    def close(self):
        try:
            if self.conn is not None:
                self.conn.close()
        except Exception:  # noqa: BLE001
            pass


def _plain_error(e: Exception) -> str:
    text = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
    if "password authentication failed" in text:
        return "The server refused the login (wrong database password in this station's connection settings)."
    if "does not exist" in text and "database" in text:
        return f"The store database was not found on the server: {text}"
    if "timeout" in text.lower() or "could not connect" in text.lower() or "connection refused" in text.lower():
        return ("The server PC could not be reached. Check that it is switched on, that PostgreSQL is running "
                "on it, and that this computer is on the shop network.")
    return text


# ---- opening a store database -------------------------------------------------------------------------
def _has_column(db: Database, table: str, column: str) -> bool:
    return bool(db.val("SELECT 1 FROM information_schema.columns WHERE table_schema = current_schema()"
                       " AND table_name = ? AND column_name = ?", (table, column)))


def _add_column(db: Database, table: str, column: str, decl: str) -> None:
    if not _has_column(db, table, column):
        db.exec(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


MIGRATIONS: dict = {}          # {2: _migrate_to_2, ...} as the layout grows


def open_database(conninfo: dict, station_id: int | None = None, template: str = "starter",
                  timezone: str = "America/Chicago", read_only: bool = False) -> Database:
    """Connect to a store database and bring it up to date.

    The first station to connect after an update runs the layout file and any migrations, inside one
    transaction holding the write lock, so two stations can never update the layout at the same time.
    A brand-new database gets the full layout and the starting lists."""
    from .. import APP_VERSION
    db = Database(conninfo, read_only=read_only, station_id=station_id, timezone=timezone)
    if read_only:
        return db
    with db.tx():
        has_meta = db.val("SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema()"
                          " AND table_name = 'meta'")
        version = db.val("SELECT value FROM meta WHERE key = 'schema_version'") if has_meta else None
        last_app = db.val("SELECT value FROM meta WHERE key = 'app_version'") if has_meta else None
        if version is not None and int(version) > SCHEMA_VERSION:
            raise RuntimeError("This store's data was updated by a newer version of OwnPOS. Install that version "
                               "(or newer) on this station.")
        if version is None or int(version) < SCHEMA_VERSION or last_app != APP_VERSION:
            if version is not None and int(version) < SCHEMA_VERSION:
                for v in range(int(version) + 1, SCHEMA_VERSION + 1):
                    MIGRATIONS[v](db)
            db.execute_script(SCHEMA_FILE.read_text(encoding="utf-8"))
            db.exec("INSERT INTO meta(key, value) VALUES('schema_version', ?) ON CONFLICT(key) DO UPDATE"
                    " SET value = excluded.value", (str(SCHEMA_VERSION),))
            if version is None:
                from .seed import seed_defaults
                seed_defaults(db, template)
            from .auth import ensure_roles
            ensure_roles(db)
            db.exec("INSERT INTO meta(key, value) VALUES('app_version', ?) ON CONFLICT(key) DO UPDATE"
                    " SET value = excluded.value", (APP_VERSION,))
    return db
