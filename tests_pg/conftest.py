"""Test setup: every test gets its own fresh store database on a real PostgreSQL server.

Set OWNPOS_TEST_PG to the server to use (default: host=127.0.0.1 port=55432 user=postgres).
A template database with the layout and starting lists is built once per run and copied for each test."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("OWNPOS_DATA", str(ROOT / ".test-data"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

PG = os.environ.get("OWNPOS_TEST_PG", "host=127.0.0.1 port=55432 user=postgres")
TEMPLATE = "ownpos_test_template"


def _admin():
    return psycopg.connect(PG + " dbname=postgres", autocommit=True)


def pg_params(dbname: str) -> dict:
    out = {}
    for part in PG.split():
        k, v = part.split("=", 1)
        out[k] = v
    out["dbname"] = dbname
    return out


@pytest.fixture(scope="session")
def template_db():
    from ownpos.core.db import open_database
    with _admin() as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEMPLATE}")
        c.execute(f"CREATE DATABASE {TEMPLATE}")
    db = open_database(pg_params(TEMPLATE), station_id=1)
    db.close()
    yield TEMPLATE
    with _admin() as c:
        c.execute(f"DROP DATABASE IF EXISTS {TEMPLATE}")


@pytest.fixture
def dbname(template_db):
    name = "ownpos_t_" + uuid.uuid4().hex[:12]
    with _admin() as c:
        c.execute(f"CREATE DATABASE {name} TEMPLATE {template_db}")
    yield name
    with _admin() as c:
        c.execute("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()",
                  (name,))
        c.execute(f"DROP DATABASE IF EXISTS {name}")


@pytest.fixture
def conninfo(dbname):
    return pg_params(dbname)


@pytest.fixture
def s(conninfo):
    """Services for station 1 with an owner logged in as s.owner."""
    from ownpos.core.services import Services
    from ownpos.core.stations import register
    sv = Services(conninfo, station_id=1)
    sv.conninfo = conninfo
    register(sv.db, 1, "Station 1")
    uid = sv.auth.create_user("owner", "Store Owner", "owner", password="owner-pass-1", pin="9999")
    sv.owner = sv.auth.get(uid)
    yield sv
    sv.close()
