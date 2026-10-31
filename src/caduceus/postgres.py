"""The Postgres store: cases, faxes, filed documents and the audit log, for a deployment.

app_factory chooses it when CADUCEUS_DATABASE_URL is set; otherwise everything stays in SQLite, as in the public demo
and the tests. The interfaces, and what they promise, are the same as SQLite's (caduceus.storage):

* A case is saved only if nobody saved it after it was read. The check and the write are one statement, so of two
  instances saving the same read, only one succeeds; the other raises StaleCase.
* The audit log is append-only in the database. A trigger refuses UPDATE, DELETE and TRUNCATE, and the service's
  role has no grant for them (infra/sql/audit.sql). Every append, from any connection or instance, takes one advisory
  lock and then reads the head of the chain, so each event follows exactly the one before it. The hashes are SQLite's,
  computed by the same function over the same text, and verify() re-walks the chain the same way.

Running it, migrating it and verifying the chain: docs/postgres.md.

    python -m caduceus.postgres migrate    apply pending migrations (in a deployment, as the owner)
    python -m caduceus.postgres verify     re-walk the audit chain
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg_pool import ConnectionPool, PoolTimeout

from caduceus.audit.log import GENESIS, AuditEvent, AuditLog, TamperDetected
from caduceus.cases import Case, Fax, StaleCase
from caduceus.migrations import MigrationError, advisory_key, migrate

log = logging.getLogger(__name__)

POOL_SIZE = 10        # connections per instance, shared by the API's worker threads and the outbox
CONNECT_TIMEOUT = 10  # seconds to wait for the server, unless the URL sets connect_timeout
AUDIT_LOCK = advisory_key("caduceus: audit appends")
row_hash = AuditLog._row_hash  # one definition of the hash for both databases, so their chains agree


class DatabaseUnavailable(RuntimeError):
    """The database CADUCEUS_DATABASE_URL names cannot be reached, or will not let the service in."""


class PostgresStore:
    """The case, fax and document repositories and the audit log, sharing one connection pool."""

    def __init__(self, pool: ConnectionPool):
        self.pool = pool
        self.cases = PostgresCaseRepository(pool)
        self.faxes = PostgresFaxRepository(pool)
        self.documents = PostgresDocumentRepository(pool)
        self.audit = PostgresAuditLog(pool)

    def close(self) -> None:
        self.pool.close()


def connect(url: str, *, pool_size: int = POOL_SIZE, apply_migrations: bool = True) -> PostgresStore:
    """Open the database, apply any pending migrations, and return the store. If the database cannot be reached this
    fails at once, saying where it tried and why, and never with the password."""
    params = _params(url)
    with _connection(params) as conn:  # reached and signed in before anything else is set up
        applied = migrate(conn) if apply_migrations else []
    if applied:
        log.info("applied migrations %s", ", ".join(m.file for m in applied))
    # Autocommit: a single statement is its own transaction, and work of several statements opens one explicitly.
    # Each connection is checked as it is handed out, so one dropped by a restart or a failover is replaced rather
    # than failing a request.
    pool = ConnectionPool(kwargs=params | {"autocommit": True}, min_size=1, max_size=pool_size, open=False,
                          configure=_configure, check=ConnectionPool.check_connection, name="caduceus")
    try:
        pool.open(wait=True, timeout=float(params["connect_timeout"]) or 30.0)  # 0 is libpq's "no limit"
    except PoolTimeout:
        pool.close()
        raise DatabaseUnavailable(f"Connected to Postgres at {_where(params)}, but could not open the service's "
                                  "connections to it in time. Check the server's connection limit.") from None
    return PostgresStore(pool)


def _params(url: str) -> dict[str, Any]:
    try:
        params = conninfo_to_dict(url)
    except psycopg.ProgrammingError:
        # The parser's message can quote the URL, password and all, so it is not passed on.
        raise DatabaseUnavailable("CADUCEUS_DATABASE_URL is not a connection URL Postgres can read.") from None
    params.setdefault("connect_timeout", CONNECT_TIMEOUT)
    params.setdefault("application_name", "caduceus")
    return params


def _connection(params: dict[str, Any]) -> psycopg.Connection:
    try:
        return psycopg.connect(**params, autocommit=True)
    except psycopg.OperationalError as e:
        # libpq's own words, which never include the password, as sentences on one line.
        lines = [" ".join(line.split()) for line in str(e).splitlines() if line.strip()]
        reason = " ".join(line if line[-1] in ".?!" else f"{line}." for line in lines)
        raise DatabaseUnavailable(f"Cannot connect to Postgres at {_where(params)}: {reason} Check "
                                  "CADUCEUS_DATABASE_URL, and that the database is running.") from None


def _where(params: dict[str, Any]) -> str:
    host = params.get("host") or params.get("hostaddr") or "the local socket"
    user = params.get("user") or "the default"
    database = params.get("dbname") or params.get("user") or "the default"  # libpq's default is the user's name
    return f"{host}, port {params.get('port') or 5432} (database {database}, user {user})"


def _configure(conn: psycopg.Connection) -> None:
    """Read committed, whatever the server's default: an append reads the head of the chain after it takes the lock,
    and must see the event committed just before, not a snapshot from before it waited."""
    conn.isolation_level = psycopg.IsolationLevel.READ_COMMITTED


def _storable(key: str) -> bool:
    """Postgres text cannot hold a NUL character, so no stored id contains one. Looking such an id up finds nothing,
    as it does on SQLite, instead of failing."""
    return "\x00" not in key


def _iso(ts: datetime) -> str:
    """A time as SQLite stores it and as it is hashed: ISO 8601 in UTC, to the microsecond, whatever the session's
    time zone."""
    return ts.astimezone(UTC).isoformat(timespec="microseconds")


class PostgresCaseRepository:
    def __init__(self, pool: ConnectionPool):
        self._pool = pool

    def save(self, case: Case) -> Case:
        new = case.model_copy(update={"version": case.version + 1})
        with self._pool.connection() as conn:
            # The version check and the write are one statement: of two saves made from the same read, the second
            # waits for the first, then finds the version moved on and writes nothing.
            saved = conn.execute(
                "INSERT INTO cases (id, state, data, version) VALUES (%(id)s, %(state)s, %(data)s, %(version)s) "
                "ON CONFLICT (id) DO UPDATE SET state = EXCLUDED.state, data = EXCLUDED.data, "
                "version = EXCLUDED.version, saved = DEFAULT WHERE cases.version = %(read)s",
                {"id": new.id, "state": new.state.value, "data": new.model_dump_json(), "version": new.version,
                 "read": case.version}).rowcount
        if saved != 1:
            raise StaleCase(f"case {case.id} was changed by someone else; reload it")
        return new

    def get(self, case_id: str) -> Case | None:
        if not _storable(case_id):
            return None
        with self._pool.connection() as conn:
            row = conn.execute("SELECT data::text FROM cases WHERE id = %s", (case_id,)).fetchone()
        return Case.model_validate(json.loads(row[0])) if row else None

    def all(self) -> list[Case]:
        with self._pool.connection() as conn:
            rows = conn.execute("SELECT data::text FROM cases ORDER BY saved").fetchall()
        return [Case.model_validate(json.loads(r[0])) for r in rows]


class PostgresFaxRepository:
    def __init__(self, pool: ConnectionPool):
        self._pool = pool

    def save(self, fax: Fax) -> Fax:
        with self._pool.connection() as conn:
            conn.execute("INSERT INTO faxes (id, data) VALUES (%s, %s) "
                         "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data", (fax.id, fax.model_dump_json()))
        return fax

    def get(self, fax_id: str) -> Fax | None:
        if not _storable(fax_id):
            return None
        with self._pool.connection() as conn:
            row = conn.execute("SELECT data::text FROM faxes WHERE id = %s", (fax_id,)).fetchone()
        return Fax.model_validate(json.loads(row[0])) if row else None

    def all(self) -> list[Fax]:
        with self._pool.connection() as conn:  # byte order, as SQLite sorts text, whatever the database's collation
            rows = conn.execute('SELECT data::text FROM faxes ORDER BY id COLLATE "C"').fetchall()
        return [Fax.model_validate(json.loads(r[0])) for r in rows]


class PostgresDocumentRepository:
    """Documents Caduceus files itself (a faxed referral's cover sheet), kept with the cases."""

    def __init__(self, pool: ConnectionPool):
        self._pool = pool

    def save(self, doc: dict[str, Any]) -> None:
        with self._pool.connection() as conn:
            conn.execute("INSERT INTO documents (id, data) VALUES (%s, %s) "
                         "ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data", (doc["id"], json.dumps(doc)))

    def get(self, doc_id: str) -> dict[str, Any] | None:
        if not _storable(doc_id):
            return None
        with self._pool.connection() as conn:
            row = conn.execute("SELECT data::text FROM documents WHERE id = %s", (doc_id,)).fetchone()
        return json.loads(row[0]) if row else None


class PostgresAuditLog:
    """The append-only, hash-chained audit log (caduceus.audit.log), in Postgres."""

    def __init__(self, pool: ConnectionPool):
        self._pool = pool

    def append(self, actor: str, role: str, action: str, case_id: str | None = None,
               detail: dict | None = None) -> AuditEvent:
        detail_json = json.dumps(detail or {}, sort_keys=True, default=str)
        with self._pool.connection() as conn, conn.transaction():
            # Every append waits here for the one before it to commit, then reads the head it left, so two writers
            # never chain onto the same event. The lock is released when this transaction ends.
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (AUDIT_LOCK,))
            head = conn.execute("SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
            prev = head[0] if head else GENESIS
            now = datetime.now(UTC)
            ts = _iso(now)
            h = row_hash(prev, ts, actor, role, case_id, action, detail_json)
            seq = conn.execute(
                "INSERT INTO audit_events (ts, actor, role, case_id, action, detail, prev_hash, hash) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING seq",
                (now, actor, role, case_id, action, detail_json, prev, h)).fetchone()[0]  # type: ignore[index]
        return AuditEvent(seq, ts, actor, role, case_id, action, json.loads(detail_json), prev, h)

    def events(self, case_id: str | None = None) -> list[AuditEvent]:
        with self._pool.connection() as conn:
            if case_id is None:
                rows = conn.execute("SELECT seq, ts, actor, role, case_id, action, detail::text, prev_hash, hash "
                                    "FROM audit_events ORDER BY seq").fetchall()
            elif _storable(case_id):
                rows = conn.execute("SELECT seq, ts, actor, role, case_id, action, detail::text, prev_hash, hash "
                                    "FROM audit_events WHERE case_id = %s ORDER BY seq", (case_id,)).fetchall()
            else:
                rows = []
        return [AuditEvent(r[0], _iso(r[1]), r[2], r[3], r[4], r[5], json.loads(r[6]), r[7], r[8]) for r in rows]

    def verify(self) -> int:
        """Re-walk the chain; returns the number of events verified or raises TamperDetected. The same walk as on
        SQLite: each event must name the one before it, and its hash must be the hash of what it says."""
        prev = GENESIS
        events = self.events()
        for e in events:
            detail_json = json.dumps(e.detail, sort_keys=True, default=str)
            if e.prev_hash != prev or e.hash != row_hash(prev, e.ts, e.actor, e.role, e.case_id, e.action,
                                                         detail_json):
                raise TamperDetected(f"audit chain broken at event {e.seq}")
            prev = e.hash
        return len(events)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m caduceus.postgres",
                                 description="Look after the Postgres database CADUCEUS_DATABASE_URL names.")
    ap.add_argument("command", choices=["migrate", "verify"],
                    help="migrate: apply pending migrations. verify: re-walk the audit chain.")
    args = ap.parse_args(argv)
    url = os.environ.get("CADUCEUS_DATABASE_URL")
    if not url:
        ap.error("set CADUCEUS_DATABASE_URL to the database, as postgresql://user:password@host:port/database")
    try:
        if args.command == "migrate":
            with _connection(_params(url)) as conn:
                applied = migrate(conn)
            print(f"Applied {', '.join(m.file for m in applied)}." if applied
                  else "Nothing to apply: the database is up to date.")
            return 0
        store = connect(url, pool_size=1, apply_migrations=False)  # an auditor's role may read, not migrate
        try:
            events = store.audit.verify()
        finally:
            store.close()
    except (DatabaseUnavailable, MigrationError) as e:
        print(e, file=sys.stderr)
        return 1
    except TamperDetected as e:
        said = str(e)
        print(f"{said[:1].upper()}{said[1:]}.", file=sys.stderr)  # "Audit chain broken at event 1042."
        return 1
    print(f"The audit chain is intact: {events} event{'s' if events != 1 else ''} verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
