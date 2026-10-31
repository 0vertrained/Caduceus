"""What Postgres itself guarantees: the audit log refuses changes, appends from many connections make one chain,
migrations apply once, a stale save from another instance is refused, and the service's role may only add to the
audit log. Each test has a database of its own on the server in CADUCEUS_TEST_DATABASE_URL."""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import urlsplit, urlunsplit

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg import sql

import caduceus.audit.log as sqlite_audit
from caduceus import postgres
from caduceus.api import create_app
from caduceus.audit.log import AuditLog
from caduceus.cases import StaleCase
from caduceus.migrations import Migration, MigrationError, available, migrate
from caduceus.seed import main as seed_main
from tests.conftest import DATA, ROOT, session_options
from tests.test_database import a_case
from tests.test_workflow_api import DOLORES

pytestmark = pytest.mark.postgres


def together(n: int, work: Callable[[int], object]) -> tuple[list[object], list[BaseException]]:
    """Run work(0) .. work(n - 1) on n threads released at the same moment; what they returned, and what they raised."""
    start, results, failures = threading.Barrier(n), [], []

    def run(i: int) -> None:
        start.wait()
        try:
            results.append(work(i))
        except BaseException as e:  # noqa: BLE001 - reported to the test, which decides
            failures.append(e)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results, failures


# The audit log --------------------------------------------------------------------------------------------------

def test_update_delete_and_truncate_are_refused_even_to_the_owner(pg):
    log = pg.store().audit
    log.append("dolores", "pa_specialist", "case.approve", "2026-1041")
    for statement in ("UPDATE audit_events SET actor = 'someone else'", "DELETE FROM audit_events WHERE seq = 1",
                      "TRUNCATE audit_events", "UPDATE audit_events SET actor = 'nobody' WHERE false"):
        with pytest.raises(psycopg.errors.RaiseException, match="audit log is append-only"):
            pg.execute(statement)
    assert log.verify() == 1


def test_the_guard_holds_in_a_session_that_skips_ordinary_triggers(pg):
    pg.store().audit.append("dolores", "pa_specialist", "case.approve", "2026-1041")
    with psycopg.connect(pg.url, autocommit=True) as conn:
        if not conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0]:
            pytest.skip("only a superuser can set session_replication_role")
        conn.execute("SET session_replication_role = replica")
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            conn.execute("DELETE FROM audit_events")


def test_eight_threads_appending_at_once_leave_one_valid_chain(pg):
    """Each thread has a pool of its own, as eight instances of the service would. They take turns at the lock."""
    logs = [pg.store().audit for _ in range(8)]

    def append_many(i: int) -> None:
        for n in range(25):
            logs[i].append(f"instance-{i}", "agent_runtime", "tool.call", f"2026-10{i:02d}", {"n": n})

    _, failures = together(8, append_many)
    assert failures == []
    events = logs[0].events()
    assert logs[0].verify() == len(events) == 200
    assert [e.prev_hash for e in events[1:]] == [e.hash for e in events[:-1]]  # one line, no branches
    actors = [e.actor for e in events]
    assert sum(a != b for a, b in zip(actors, actors[1:], strict=False)) > 8  # they did interleave
    for i in range(8):
        assert [e.detail["n"] for e in events if e.actor == f"instance-{i}"] == list(range(25))


def test_a_writer_that_skips_the_lock_still_cannot_fork_the_chain(pg):
    """Two writers that read the same head both try to follow it: the database keeps one and refuses the other."""
    head = pg.store().audit.append("dolores", "pa_specialist", "case.viewed", "2026-1041")
    insert = ("INSERT INTO audit_events (ts, actor, role, case_id, action, detail, prev_hash, hash) "
              "VALUES (now(), 'mallory', 'agent_runtime', NULL, 'case.viewed', '{}', %s, %s)")
    with psycopg.connect(pg.url, autocommit=True) as conn:
        conn.execute(insert, (head.hash, "1" * 64))
        with pytest.raises(psycopg.errors.UniqueViolation, match="audit_no_fork"):
            conn.execute(insert, (head.hash, "2" * 64))


class Clock(datetime):
    """Stands in for datetime in an audit module, so both databases record the same times."""

    times: list[datetime] = []

    @classmethod
    def now(cls, tz=None):
        return cls.times.pop(0)


TIMES = [
    datetime(2026, 3, 8, 8, 59, 59, 999999, tzinfo=UTC),  # a microsecond before Denver's clocks go forward
    datetime(2026, 3, 8, 9, 0, tzinfo=UTC),                 # the moment they do, with no microseconds at all
    datetime(2026, 11, 1, 7, 30, tzinfo=UTC),               # 1:30 am in Denver, the first time that night
    datetime(2026, 11, 1, 8, 30, 0, 500, tzinfo=UTC),       # 1:30 am again, an hour later
    datetime(2026, 10, 29, 19, 0, 0, 123456, tzinfo=UTC),
]
DETAILS = [None, {"b": 1, "a": [1, 2.5, None, True]}, {"text": "café ° \x00 \U0001f9fe", "sha256": "ab" * 32},
           {"big": 1e16, "zero": -0.0, "nested": {"z": {}, "y": []}}, {"at": datetime(2026, 10, 29, tzinfo=UTC)}]


def test_the_same_appends_give_the_same_hashes_on_sqlite_and_postgres(pg, monkeypatch):
    """The Postgres session runs in Denver here, so instants around its clock changes must read back exactly."""
    for module in (sqlite_audit, postgres):
        monkeypatch.setattr(module, "datetime", type("Clock", (Clock,), {"times": list(TIMES)}))
    logs = {"sqlite": AuditLog(sqlite3.connect(":memory:")), "postgres": pg.store("-c TimeZone=America/Denver").audit}
    made = {name: [log.append("dolores", "pa_specialist", f"step.{i}", "2026-1043" if i % 2 else None, detail)
                   for i, detail in enumerate(DETAILS)] for name, log in logs.items()}

    assert [e.hash for e in made["sqlite"]] == [e.hash for e in made["postgres"]]
    for name, log in logs.items():
        assert log.events() == made[name] and log.verify() == len(TIMES)  # read back exactly as appended
    assert made["postgres"][1].ts == "2026-03-08T09:00:00.000000+00:00"
    assert [e.ts for e in made["postgres"]] == [t.isoformat(timespec="microseconds") for t in TIMES]


# Migrations -----------------------------------------------------------------------------------------------------

def test_migrations_apply_once_and_are_skipped_after(pg):
    with psycopg.connect(pg.url, autocommit=True) as conn:
        assert [m.file for m in migrate(conn)] == [m.file for m in available()]
        assert migrate(conn) == []
        recorded = conn.execute("SELECT version, file, sha256 FROM schema_migrations ORDER BY version").fetchall()
    assert recorded == [(m.version, m.file, m.sha256) for m in available()]
    for _ in range(2):  # the service starting, twice: it finds nothing to apply, and the tables work
        store = pg.store()
        store.cases.save(a_case(f"2026-{len(store.cases.all()) + 1:04d}"))
    assert len(pg.store().cases.all()) == 2


def test_instances_starting_together_apply_each_migration_once(pg):
    conns = [psycopg.connect(pg.url, autocommit=True) for _ in range(6)]  # connected first, released at once
    try:
        results, failures = together(6, lambda i: [m.file for m in migrate(conns[i])])
    finally:
        for conn in conns:
            conn.close()
    assert failures == []
    assert sorted(results) == [[]] * 5 + [[m.file for m in available()]]


def test_new_migrations_apply_in_order_and_one_that_fails_changes_nothing(pg):
    later = [*available(), Migration.of("0003_reminder_notes.sql", "ALTER TABLE reminders ADD COLUMN note TEXT;"),
             Migration.of("0002_reminders.sql", "CREATE TABLE reminders (id TEXT PRIMARY KEY);")]
    broken = [*later, Migration.of("0004_more.sql", "CREATE TABLE more (id TEXT); SELECT no_such_function();")]
    with psycopg.connect(pg.url, autocommit=True) as conn:
        migrate(conn)
        assert [m.file for m in migrate(conn, later)] == ["0002_reminders.sql", "0003_reminder_notes.sql"]
        with pytest.raises(MigrationError, match="0004_more.sql did not apply, so this run changed nothing"):
            migrate(conn, broken)
        assert conn.execute("SELECT to_regclass('more')").fetchone()[0] is None
        assert conn.execute("SELECT max(version) FROM schema_migrations").fetchone()[0] == 3


def test_a_migration_changed_after_it_was_applied_is_refused(pg):
    edited = Migration.of("0001_initial.sql", available()[0].sql + "\n-- an afterthought\n")
    with psycopg.connect(pg.url, autocommit=True) as conn:
        migrate(conn)
        with pytest.raises(MigrationError, match="0001_initial.sql was changed after it was applied"):
            migrate(conn, [edited])


# Cases ----------------------------------------------------------------------------------------------------------

def test_a_stale_save_from_another_instance_is_refused(pg):
    here, there = pg.store().cases, pg.store().cases  # two instances, each with its own connections
    here.save(a_case())
    mine, theirs = here.get("2026-1043"), there.get("2026-1043")
    there.save(theirs.model_copy(update={"returned_reason": "from the other instance"}))
    with pytest.raises(StaleCase):
        here.save(mine.model_copy(update={"returned_reason": "from this one"}))
    assert here.get("2026-1043").returned_reason == "from the other instance"


def test_of_eight_saves_racing_from_one_read_exactly_one_lands(pg):
    instances = [pg.store().cases for _ in range(8)]
    instances[0].save(a_case())
    read = instances[0].get("2026-1043")

    def save(i: int):
        return instances[i].save(read.model_copy(update={"returned_reason": f"instance {i}"}))

    won, failures = together(8, save)
    assert len(won) == 1 and len(failures) == 7 and all(isinstance(e, StaleCase) for e in failures)
    kept = instances[0].get("2026-1043")
    assert kept.version == 2 and kept.returned_reason == won[0].returned_reason


# Connecting -----------------------------------------------------------------------------------------------------

def test_a_wrong_password_is_a_clear_error_that_never_shows_it(pg):
    parts = urlsplit(pg.url)
    if not (parts.username and parts.hostname):
        pytest.skip("the test server's URL names no user and host to sign in to")
    host = f"{parts.hostname}:{parts.port}" if parts.port else parts.hostname
    wrong = urlunsplit(parts._replace(netloc=f"{parts.username}:not-the-password-0451@{host}"))
    try:
        psycopg.connect(wrong).close()
    except psycopg.OperationalError:
        pass
    else:
        pytest.skip("this server lets any password in")
    with pytest.raises(postgres.DatabaseUnavailable, match="password authentication failed") as refused:
        postgres.connect(wrong)
    assert "not-the-password-0451" not in str(refused.value)


def test_the_api_gives_back_the_connections_it_opened_when_it_stops(pg, monkeypatch):
    monkeypatch.setenv("CADUCEUS_DATABASE_URL", pg.url)
    monkeypatch.setenv("CADUCEUS_AUTH_MODE", "dev")
    monkeypatch.setenv("CADUCEUS_DATA_DIR", str(DATA))
    with TestClient(create_app(dispatch_interval=None)) as client:
        assert client.get("/api/queue", headers=DOLORES).status_code == 200
        pool = client.app.state.live.svc.database.pool
        assert not pool.closed
    assert pool.closed


# The command line -----------------------------------------------------------------------------------------------

def test_seeding_fills_an_empty_database_once(pg, monkeypatch, capsys):
    monkeypatch.setenv("CADUCEUS_DATABASE_URL", pg.url)
    monkeypatch.setenv("CADUCEUS_DATA_DIR", str(DATA))
    seed_main(["--if-missing"])
    said = capsys.readouterr().out
    assert said.startswith("Seeded 8 cases into Postgres; audit chain verified: ") and pg.url not in said
    seed_main(["--if-missing"])
    assert capsys.readouterr().out == "The database already holds cases; leaving it as it is.\n"
    with pytest.raises(SystemExit, match="its audit log cannot be cleared"):
        seed_main([])


def test_migrate_and_verify_from_the_command_line(pg, monkeypatch, capsys):
    monkeypatch.setenv("CADUCEUS_DATABASE_URL", pg.url)
    assert postgres.main(["migrate"]) == 0
    assert capsys.readouterr().out == f"Applied {', '.join(m.file for m in available())}.\n"
    assert postgres.main(["migrate"]) == 0
    assert capsys.readouterr().out == "Nothing to apply: the database is up to date.\n"
    log = pg.store().audit
    for case_id in ("2026-1041", "2026-1036"):
        log.append("dolores", "pa_specialist", "case.approve", case_id)
    assert postgres.main(["verify"]) == 0
    assert capsys.readouterr().out == "The audit chain is intact: 2 events verified.\n"
    pg.bypass_append_only()
    pg.execute("UPDATE audit_events SET actor = 'mallory' WHERE seq = 2")
    assert postgres.main(["verify"]) == 1
    assert capsys.readouterr().err == "Audit chain broken at event 2.\n"


# The deployment's roles (infra/sql/audit.sql) -------------------------------------------------------------------

ROLES = ("caduceus_app", "caduceus_auditor")


@pytest.fixture()
def roles(pg):
    """The deployment's roles, granted what infra/sql/audit.sql grants, on this test's database. Roles belong to the
    whole server, so any this test makes are dropped again; ones that were already there are left as they were."""
    with psycopg.connect(pg.url, autocommit=True) as conn:
        me = conn.execute("SELECT rolsuper OR rolcreaterole FROM pg_roles WHERE rolname = current_user").fetchone()
        if not me[0]:
            pytest.skip("making the deployment's roles needs CREATEROLE")
        made = [r for r in ROLES if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (r,)).fetchone() is None]
        for role in made:
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
    pg.store()  # the owner applies the migrations
    pg.execute((ROOT / "infra" / "sql" / "audit.sql").read_text())
    yield
    pg.close()  # no session is still signed in as one of them
    with psycopg.connect(pg.url, autocommit=True) as conn:
        for role in made:
            conn.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))  # its grants on this database
            conn.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def granted(conn: psycopg.Connection, role: str, table: str) -> set[str]:
    kinds = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
    return {k for k in kinds if conn.execute("SELECT has_table_privilege(%s, %s, %s)", (role, table, k)).fetchone()[0]}


def test_the_app_role_may_add_to_the_audit_log_but_never_change_it(pg, roles):
    with psycopg.connect(pg.url, autocommit=True) as conn:
        assert granted(conn, "caduceus_app", "audit_events") == {"SELECT", "INSERT"}
        assert granted(conn, "caduceus_auditor", "audit_events") == {"SELECT"}
        for table in ("cases", "faxes", "documents"):
            assert granted(conn, "caduceus_app", table) == {"SELECT", "INSERT", "UPDATE"}
            assert granted(conn, "caduceus_auditor", table) == set()
        assert granted(conn, "caduceus_app", "schema_migrations") == {"SELECT"}

    app = pg.store("-c role=caduceus_app")  # starts as the service would: the migrations are current, nothing to apply
    app.cases.save(a_case())
    app.audit.append("dolores", "pa_specialist", "case.approve", "2026-1043")
    assert app.audit.verify() == 1
    with psycopg.connect(pg.url, autocommit=True) as conn:
        conn.execute("SET ROLE caduceus_app")
        for statement in ("UPDATE audit_events SET actor = 'someone else'", "DELETE FROM audit_events",
                          "TRUNCATE audit_events", "ALTER TABLE audit_events DISABLE TRIGGER audit_append_only",
                          "DROP TABLE audit_events"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)

    # The auditor verifies the chain as `python -m caduceus.postgres verify` does, applying no migrations.
    auditor = postgres.connect(session_options(pg.url, "-c role=caduceus_auditor"), apply_migrations=False)
    try:
        assert auditor.audit.verify() == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            auditor.audit.append("sam", "auditor", "case.viewed", "2026-1043")
    finally:
        auditor.close()


def test_a_pending_migration_needs_the_owner_and_says_so(pg, roles):
    pending = [*available(), Migration.of("0002_reminders.sql", "CREATE TABLE reminders (id TEXT PRIMARY KEY);")]
    with psycopg.connect(pg.url, autocommit=True) as conn:
        conn.execute("SET ROLE caduceus_app")
        with pytest.raises(MigrationError, match="may not apply 0002_reminders.sql .*as the database owner"):
            migrate(conn, pending)
        conn.execute("RESET ROLE")
        assert [m.file for m in migrate(conn, pending)] == ["0002_reminders.sql"]
