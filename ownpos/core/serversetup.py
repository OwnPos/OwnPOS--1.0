"""Setting up the store server (run once, on the server PC, after installing PostgreSQL).

1. create_store(): with the PostgreSQL administrator password (the one chosen when PostgreSQL was installed),
   make a login just for OwnPOS ("ownpos", with a long random password) and the store database. OwnPOS never
   keeps the administrator password.
2. allow_network(): let the stations reach the server. This changes two Windows things, so it runs with
   administrator rights (Windows asks): one line in PostgreSQL's pg_hba.conf allowing the "ownpos" login from
   the shop's own network only ("samenet"), with a password (scram-sha-256); and a Windows Firewall rule for
   PostgreSQL's port on Private networks only.
3. station_file(): a small file with the connection details for setting up the other stations from a USB stick.
   It holds the OwnPOS database password, so delete it from the stick afterwards."""
from __future__ import annotations

import json
import re
import secrets
import string
from pathlib import Path

import psycopg
from psycopg import sql

HBA_MARK = "# OwnPOS: stations on this shop network"


class SetupError(Exception):
    pass


def _admin(host: str, port: int, admin_user: str, admin_password: str, dbname: str = "postgres"):
    try:
        return psycopg.connect(host=host, port=port, user=admin_user, password=admin_password, dbname=dbname,
                               autocommit=True, connect_timeout=5)
    except psycopg.OperationalError as e:
        text = str(e)
        if "password authentication failed" in text:
            raise SetupError("PostgreSQL refused the administrator password. Use the password chosen when "
                             "PostgreSQL was installed (user 'postgres').") from e
        raise SetupError(f"Could not reach PostgreSQL on {host}:{port}: {text.splitlines()[0]}") from e


def new_password(n: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def create_store(host: str, port: int, admin_user: str, admin_password: str, dbname: str = "ownpos_store",
                 role: str = "ownpos", role_password: str | None = None, demo: bool = False) -> dict:
    """Create the OwnPOS login and the store database (and optionally the sample/demo database).
    Safe to run again: an existing login gets a new password; an existing database is left as it is."""
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", dbname) or not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise SetupError("Names use lower-case letters, digits and _ only")
    role_password = role_password or new_password()
    made = []
    with _admin(host, port, admin_user, admin_password) as c:
        if c.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
            c.execute(sql.SQL("ALTER ROLE {} LOGIN CREATEDB PASSWORD {}").format(sql.Identifier(role),
                                                                                  sql.Literal(role_password)))
        else:
            c.execute(sql.SQL("CREATE ROLE {} LOGIN CREATEDB PASSWORD {}").format(sql.Identifier(role),
                                                                                   sql.Literal(role_password)))
        names = [dbname] + ([dbname.replace("store", "demo") if "store" in dbname else dbname + "_demo"] if demo else [])
        for name in names:
            if not c.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone():
                c.execute(sql.SQL("CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0").format(
                    sql.Identifier(name), sql.Identifier(role)))
                made.append(name)
        listen = c.execute("SHOW listen_addresses").fetchone()[0]
        hba = c.execute("SHOW hba_file").fetchone()[0]
        version = c.execute("SHOW server_version").fetchone()[0]
    return {"host": host, "port": port, "dbname": dbname, "user": role, "password": role_password,
            "created": made, "listen_addresses": listen, "hba_file": hba, "server_version": version,
            "demo_db": names[1] if demo else None}


def hba_line(databases: list[str], role: str = "ownpos") -> str:
    return f"host    {','.join(databases)}    {role}    samenet    scram-sha-256    {HBA_MARK}"


def add_hba_line(hba_file: str | Path, databases: list[str], role: str = "ownpos") -> bool:
    """Append the OwnPOS line to pg_hba.conf if it is not there. Keeps a .ownpos-backup copy first.
    Returns True if the file changed."""
    p = Path(hba_file)
    text = p.read_text(encoding="utf-8", errors="replace")
    line = hba_line(databases, role)
    if any(HBA_MARK in ln for ln in text.splitlines()):
        new = "\n".join(line if HBA_MARK in ln else ln for ln in text.splitlines()) + "\n"
        if new == text:
            return False
    else:
        new = text.rstrip("\n") + "\n\n" + line + "\n"
    p.with_suffix(p.suffix + ".ownpos-backup").write_text(text, encoding="utf-8")
    p.write_text(new, encoding="utf-8")
    return True


def station_file(path: str | Path, info: dict) -> Path:
    data = {k: info[k] for k in ("host", "port", "dbname", "user", "password")}
    data["note"] = ("OwnPOS station setup file. It contains the store database password: delete it from the USB "
                    "stick after setting up the stations.")
    p = Path(path)
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


def read_station_file(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [k for k in ("host", "port", "dbname", "user", "password") if not data.get(k)]
    if missing:
        raise SetupError("This is not an OwnPOS station setup file")
    return data


def postgres_services() -> list[str]:
    """Names of the PostgreSQL Windows services on this PC (e.g. postgresql-x64-17)."""
    import subprocess
    r = subprocess.run(["sc", "query", "state=", "all"], capture_output=True, text=True)
    return [ln.split(":", 1)[1].strip() for ln in r.stdout.splitlines()
            if ln.strip().upper().startswith("SERVICE_NAME") and "postgres" in ln.lower()]


def allow_network_windows(hba_file: str, port: int, databases: list[str], role: str = "ownpos",
                          restart: bool = False) -> list[str]:
    """Run with administrator rights on the server PC: pg_hba.conf line, firewall rule (Private networks only),
    and (when listen_addresses had to change) a restart of the PostgreSQL service. Returns what was done."""
    import subprocess
    done = []
    if add_hba_line(hba_file, databases, role):
        done.append(f"added a line to {hba_file}")
    rule = "OwnPOS PostgreSQL"
    subprocess.run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={rule}"], capture_output=True)
    r = subprocess.run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={rule}", "dir=in", "action=allow",
                        "protocol=TCP", f"localport={int(port)}", "profile=private"], capture_output=True, text=True)
    done.append("firewall rule added (Private networks)" if r.returncode == 0 else f"firewall: {r.stdout or r.stderr}")
    if restart:
        for name in postgres_services():
            subprocess.run(["net", "stop", name], capture_output=True)
            r = subprocess.run(["net", "start", name], capture_output=True, text=True)
            done.append(f"restarted {name}" if r.returncode == 0 else f"could not restart {name}: {r.stdout}")
    return done
