"""Backups of the store database with PostgreSQL's own tools (pg_dump / pg_restore).

A backup is a single .ownpos-backup file (pg_dump custom format). After writing it, the file is read back
with `pg_restore --list` to prove it is complete; a file that fails that check is deleted and the backup
reported as failed. Copies go to the backup folder and, if set, a second folder (USB / cloud-synced).

Restoring never touches the live database: the backup is restored into a NEW database, checked (tables
present, ledger balanced), and only then can the owner switch the stations to it."""
from __future__ import annotations

import glob
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .db import Database, now_str
from .paths import data_root

SUFFIX = ".ownpos-backup"


class BackupError(Exception):
    pass


def find_tool(db: Database | None, name: str) -> str:
    """Path of pg_dump / pg_restore / psql: Settings > Backups > PostgreSQL tools folder, else the newest
    PostgreSQL installed in Program Files, else the PATH."""
    exe = name + (".exe" if sys.platform == "win32" else "")
    folder = (db.get_setting("pg_bin_folder") if db is not None else "") or os.environ.get("OWNPOS_PG_BIN", "")
    if folder and (Path(folder) / exe).exists():
        return str(Path(folder) / exe)
    candidates = []
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), r"C:\Program Files"):
        candidates += glob.glob(str(Path(base) / "PostgreSQL" / "*" / "bin" / exe))
    candidates += glob.glob(f"/usr/lib/postgresql/*/bin/{exe}")

    def version(p):
        m = re.search(r"[\\/](\d+)(?:\.\d+)?[\\/]bin", p)
        return int(m.group(1)) if m else 0
    if candidates:
        return sorted(candidates, key=version)[-1]
    found = shutil.which(exe)
    if found:
        return found
    raise BackupError(f"{name} was not found. Install PostgreSQL's command line tools on this PC, or set the "
                      "folder in Settings > Backups.")


def _env(conninfo: dict) -> dict:
    env = dict(os.environ)
    if conninfo.get("password"):
        env["PGPASSWORD"] = str(conninfo["password"])
    return env


def _args(conninfo: dict) -> list[str]:
    out = []
    for flag, key in (("-h", "host"), ("-p", "port"), ("-U", "user")):
        if conninfo.get(key):
            out += [flag, str(conninfo[key])]
    return out


def default_backup_folder(db: Database) -> Path:
    chosen = (db.get_setting("backup_folder") or "").strip()
    if chosen and Path(chosen).exists():
        return Path(chosen)
    p = data_root() / "backups"
    p.mkdir(parents=True, exist_ok=True)
    return p


def backup(db: Database, conninfo: dict, label: str = "manual", folder: Path | None = None) -> Path:
    if conninfo is None:
        raise BackupError("This station has no server connection to back up")
    folder = Path(folder) if folder else default_backup_folder(db)
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = folder / f"{conninfo.get('dbname', 'ownpos')}_{stamp}_{label}{SUFFIX}"
    pg_dump = find_tool(db, "pg_dump")
    run = subprocess.run([pg_dump, *_args(conninfo), "-Fc", "-Z", "6", "-f", str(target), conninfo["dbname"]],
                         env=_env(conninfo), capture_output=True, text=True, timeout=1800,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if run.returncode != 0 or not target.exists():
        target.unlink(missing_ok=True)
        raise BackupError("The backup did not finish: " + (run.stderr.strip() or "pg_dump failed"))
    verify(db, target)
    return target


def verify(db: Database | None, path: Path) -> int:
    """Read the backup's table of contents; returns the number of table-data entries. Raises if unreadable."""
    pg_restore = find_tool(db, "pg_restore")
    run = subprocess.run([pg_restore, "--list", str(path)], capture_output=True, text=True, timeout=600,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if run.returncode != 0:
        Path(path).unlink(missing_ok=True)
        raise BackupError("The backup file could not be read back, so it was deleted: " + run.stderr.strip())
    data_entries = sum(1 for line in run.stdout.splitlines() if " TABLE DATA " in line)
    if data_entries < 10:
        raise BackupError("The backup file looks incomplete (too few tables)")
    return data_entries


def prune(folder: Path, keep: int) -> None:
    files = sorted(Path(folder).glob(f"*{SUFFIX}"), key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[keep:]:
        try:
            f.unlink()
        except OSError:
            pass


def backup_all(db: Database, conninfo: dict, label: str = "auto") -> list[Path]:
    """Backup to the main folder and copy to the second folder. Records the time."""
    path = backup(db, conninfo, label)
    out = [path]
    keep = int(db.get_setting("backup_keep", 30) or 30)
    prune(path.parent, keep)
    extra = (db.get_setting("backup_extra_folder") or "").strip()
    if extra:
        try:
            Path(extra).mkdir(parents=True, exist_ok=True)
            dest = Path(extra) / path.name
            shutil.copy2(path, dest)
            out.append(dest)
            prune(Path(extra), keep)
        except OSError as e:
            raise BackupError(f"Backed up to {path}, but the copy to the second folder failed: {e}") from e
    db.set_setting("last_backup_at", now_str())
    db.set_setting("last_backup_file", str(path))
    return out


def restore_to_new_database(db: Database, conninfo: dict, backup_file: Path, new_name: str | None = None) -> str:
    """Restore a backup into a brand-new database on the same server and check it. Returns the new name.
    The live database is not touched; switching the stations to the new one is a separate, deliberate step."""
    backup_file = Path(backup_file)
    if not backup_file.exists():
        raise BackupError("Backup file not found")
    verify(db, backup_file)
    new_name = new_name or f"{conninfo['dbname']}_restored_{datetime.now():%Y%m%d_%H%M%S}"
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", new_name):
        raise BackupError("Database names use lower-case letters, digits and _ only")
    import psycopg
    admin = dict(conninfo, dbname="postgres")
    try:
        with psycopg.connect(autocommit=True, connect_timeout=5,
                             **{k: v for k, v in admin.items() if v not in (None, "")}) as c:
            if c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (new_name,)).fetchone():
                raise BackupError(f"A database called {new_name} already exists")
            c.execute(f'CREATE DATABASE "{new_name}"')
    except psycopg.Error as e:
        raise BackupError(f"Could not create the database for the restore: {e}") from e
    pg_restore = find_tool(db, "pg_restore")
    run = subprocess.run([pg_restore, *_args(conninfo), "--no-owner", "-d", new_name, str(backup_file)],
                         env=_env(conninfo), capture_output=True, text=True, timeout=3600,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if run.returncode != 0:
        raise BackupError("The restore reported errors; the new database was left for inspection, the live one is "
                          "untouched: " + run.stderr.strip()[:500])
    check = Database(dict(conninfo, dbname=new_name), read_only=True)
    try:
        d, c = check.one("SELECT COALESCE(SUM(debit),0), COALESCE(SUM(credit),0) FROM journal_lines")
        if d != c:
            raise BackupError("The restored database's ledger does not balance; do not use it")
        n = check.val("SELECT COUNT(*) FROM sales", default=0)
    finally:
        check.close()
    db.set_setting("last_restore", {"file": str(backup_file), "into": new_name, "at": now_str(), "receipts": n})
    return new_name
