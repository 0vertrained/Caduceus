from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import psycopg
import pytest
from psycopg import sql

from caduceus import postgres
from caduceus.app_factory import Services, Settings, build_services
from caduceus.audit.log import AuditLog
from caduceus.cases import CaseRepository, DocumentRepository, FaxRepository
from caduceus.criteria.engine import Engine
from caduceus.criteria.loader import load_library, load_lock
from caduceus.deid.safe_harbor import SafeHarborDeidentifier
from caduceus.fhir.store import LocalBundleStore

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
TEST_KEY = b"test-signing-key-that-is-at-least-32-bytes-long"

# The tests choose their database themselves (the backend fixture below). A database named in the environment for
# running the service is never used, so the suite cannot write into it.
os.environ.pop("CADUCEUS_DATABASE_URL", None)

# A Postgres server to run the database tests on as well, as postgresql://user:password@host:port/database. Its user
# must be able to create a database: the run makes one of its own, which each test starts empty, and drops it at the
# end. The database in the URL is only used to make and drop that one.
PG_URL = os.environ.get("CADUCEUS_TEST_DATABASE_URL") or None
BACKENDS = ["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)] if PG_URL else ["sqlite"]

# Every Postgres test runs in a session whose time zone is far from UTC, so what is read back, and with it every hash,
# is shown not to depend on the session. Commits do not wait for the disk, which no test needs.
SESSION = "-c TimeZone=Asia/Kathmandu -c synchronous_commit=off"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "postgres: runs on the Postgres server in CADUCEUS_TEST_DATABASE_URL")


def session_options(url: str, options: str) -> str:
    """The same URL, with these settings added to the session's options; a later setting wins."""
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query["options"] = " ".join(filter(None, [query.get("options"), options]))
    return urlunsplit(parts._replace(query=urlencode(query, quote_via=quote)))


@pytest.fixture(scope="session")
def pg_database() -> Iterator[str]:
    """The run's own database on the test server. Made once and dropped once: dropping a database makes the server
    write a checkpoint, too slow to do for every test."""
    name = f"caduceus_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(PG_URL, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield session_options(urlunsplit(urlsplit(PG_URL)._replace(path=f"/{name}")), SESSION)
    finally:
        with psycopg.connect(PG_URL, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def emptied(url: str) -> str:
    """The run's database as a new one is: its public schema dropped with everything in it, and made again as
    Postgres makes it (owned by the database's owner; anyone may use it, only the owner may create in it)."""
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public AUTHORIZATION pg_database_owner; "
                     "GRANT USAGE ON SCHEMA public TO PUBLIC")
    return url


class Backend:
    """The database one test runs on: SQLite in memory, or a Postgres database of the test's own. Also the ways in
    that a test needs, as someone with direct access to the database would have them."""

    def __init__(self, url: str | None = None):
        self.name = "sqlite" if url is None else "postgres"
        self.url = url
        self.conn = sqlite3.connect(":memory:", check_same_thread=False) if url is None else None
        self.error: type[Exception] = sqlite3.DatabaseError if url is None else psycopg.Error
        self._closing: list[Callable[[], None]] = []  # Postgres pools, closed before the database is dropped

    def services(self, **settings: Any) -> Services:
        """The whole service on this database, as build_services makes it."""
        svc = build_services(Settings(data_dir=DATA, db_path=":memory:", database_url=self.url, **settings),
                             conn=self.conn)
        self._closing.append(svc.close)
        return svc

    def store(self, options: str = "") -> Any:
        """The repositories and the audit log on this database, without the rest of the service. On Postgres each
        call opens a pool of its own, as another instance of the service would; `options` sets its sessions up."""
        if self.conn is not None:
            return SimpleNamespace(cases=CaseRepository(self.conn), faxes=FaxRepository(self.conn),
                                   documents=DocumentRepository(self.conn), audit=AuditLog(self.conn))
        store = postgres.connect(session_options(self.url, options) if options else self.url)
        self._closing.append(store.close)
        return store

    def execute(self, statement: str) -> None:
        if self.conn is not None:
            self.conn.execute(statement)
            return
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute(statement)

    def bypass_append_only(self) -> None:
        """What someone with rights to change the schema would do first: switch the append-only guard off."""
        self.execute("DROP TRIGGER audit_no_update" if self.conn is not None
                     else "ALTER TABLE audit_events DISABLE TRIGGER audit_append_only")

    def close(self) -> None:
        while self._closing:
            self._closing.pop()()


@pytest.fixture(params=BACKENDS)
def backend(request: pytest.FixtureRequest) -> Iterator[Backend]:
    """SQLite always; Postgres as well when CADUCEUS_TEST_DATABASE_URL names a server. Each test starts empty."""
    b = Backend(None if request.param == "sqlite" else emptied(request.getfixturevalue("pg_database")))
    yield b
    b.close()


@pytest.fixture()
def pg(request: pytest.FixtureRequest) -> Iterator[Backend]:
    """An empty Postgres database, for what only Postgres does."""
    if PG_URL is None:
        pytest.skip("set CADUCEUS_TEST_DATABASE_URL to run the Postgres tests")
    b = Backend(emptied(request.getfixturevalue("pg_database")))
    yield b
    b.close()


@pytest.fixture(scope="session")
def library():
    return load_library(DATA / "policies")


@pytest.fixture(scope="session")
def engine():
    return Engine(load_lock(DATA / "policies"))


@pytest.fixture(scope="session")
def store():
    return LocalBundleStore(DATA)


@pytest.fixture(scope="session")
def cases():
    return json.loads((DATA / "cases" / "cases.json").read_text())


@pytest.fixture()
def deid():
    return SafeHarborDeidentifier(TEST_KEY)
