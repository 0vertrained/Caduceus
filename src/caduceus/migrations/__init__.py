"""Versioned SQL migrations for the Postgres store.

Each file here is named NNNN_what_it_does.sql and is applied once, in order of its number. schema_migrations records
each one applied, with a hash of its text, so a migration edited after it ran is refused rather than skipped: a change
goes in a new file. Pending migrations run in one transaction, under an advisory lock, so two instances starting at
once cannot both apply one, and a migration that fails leaves the database as it was.

The service applies what is pending when it starts. In a deployment it connects as a role that may not change the
schema (infra/sql/audit.sql), so the owner applies them first and the service finds nothing to do:

    python -m caduceus.postgres migrate
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from importlib import resources

import psycopg

FILENAME = re.compile(r"(\d{4})_([a-z0-9_]+)\.sql")

SCHEMA_MIGRATIONS = """
CREATE TABLE schema_migrations (
    version     INTEGER PRIMARY KEY,
    file        TEXT NOT NULL,
    sha256      CHAR(64) NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def advisory_key(name: str) -> int:
    """A fixed 64-bit key for pg_advisory_xact_lock, the same in every instance. Postgres keeps one set of advisory
    locks per database, so instances sharing a database share the lock."""
    return int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)


LOCK = advisory_key("caduceus: schema migrations")


class MigrationError(RuntimeError):
    """The database's migrations cannot be brought up to date; the message says why and what to do."""


@dataclass(frozen=True)
class Migration:
    version: int
    file: str
    sql: str
    sha256: str

    @classmethod
    def of(cls, file: str, sql: str) -> Migration:
        match = FILENAME.fullmatch(file)
        if match is None:
            raise MigrationError(f"{file} is not named like 0001_what_it_does.sql, so it would never be applied.")
        sql = sql.replace("\r\n", "\n")  # a checkout with Windows line endings is the same migration
        return cls(int(match[1]), file, sql, hashlib.sha256(sql.encode()).hexdigest())


def available() -> list[Migration]:
    """The migrations shipped with this version of Caduceus, in order."""
    found = sorted((Migration.of(f.name, f.read_text(encoding="utf-8"))
                    for f in resources.files(__name__).iterdir() if f.name.endswith(".sql")),
                   key=lambda m: m.version)
    versions = [m.version for m in found]
    if len(set(versions)) != len(versions):
        raise MigrationError("Two migration files share a number; each number is applied once.")
    return found


def migrate(conn: psycopg.Connection, migrations: list[Migration] | None = None) -> list[Migration]:
    """Apply the migrations this database has not had yet, and return them (none when it is up to date).

    `conn` must not be inside a transaction. The run reads committed data statement by statement, so an instance that
    waited on the lock sees what the instance before it applied."""
    todo = available() if migrations is None else sorted(migrations, key=lambda m: m.version)
    applied: list[Migration] = []
    step = "read schema_migrations"
    try:
        with conn.transaction():
            conn.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
            if conn.execute("SELECT to_regclass('schema_migrations')").fetchone()[0] is None:  # type: ignore[index]
                step = "create schema_migrations"
                conn.execute(SCHEMA_MIGRATIONS)
            done = dict(conn.execute("SELECT version, sha256 FROM schema_migrations").fetchall())
            for m in todo:
                if m.version in done:
                    if done[m.version] != m.sha256:
                        raise MigrationError(f"{m.file} was changed after it was applied to this database. An applied "
                                             "migration is never edited: put the change in a new migration.")
                    continue
                step = f"apply {m.file}"
                try:
                    conn.execute(m.sql)  # type: ignore[arg-type]  # the package's own file, not built from input
                except (psycopg.errors.InsufficientPrivilege, psycopg.OperationalError):
                    raise
                except psycopg.Error as e:
                    raise MigrationError(f"{m.file} did not apply, so this run changed nothing: {e}") from e
                conn.execute("INSERT INTO schema_migrations (version, file, sha256) VALUES (%s, %s, %s)",
                             (m.version, m.file, m.sha256))
                applied.append(m)
    except psycopg.errors.InsufficientPrivilege as e:
        raise MigrationError(f"This database role may not {step} ({str(e).splitlines()[0]}). Apply migrations as the "
                             "database owner (python -m caduceus.postgres migrate), and grant the service's role what "
                             "infra/sql/audit.sql lists.") from e
    return applied
