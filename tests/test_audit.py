"""The audit log is append-only and tamper-evident."""

from __future__ import annotations

import sqlite3

import pytest

from caduceus.audit.log import AuditLog, TamperDetected


@pytest.fixture()
def log():
    return AuditLog(sqlite3.connect(":memory:", check_same_thread=False))


def test_append_and_verify(log):
    log.append("dolores", "pa_specialist", "case.open", "2026-1043", {"view": "case"})
    log.append("agent", "agent_runtime", "criteria.evaluated", "2026-1043", {"overall": "needs_attestation"})
    assert log.verify() == 2
    assert [e.action for e in log.events("2026-1043")] == ["case.open", "criteria.evaluated"]


def test_update_and_delete_are_refused_by_the_database(log):
    log.append("dolores", "pa_specialist", "case.approve", "2026-1041")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        log._conn.execute("UPDATE audit_events SET actor = 'someone else'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        log._conn.execute("DELETE FROM audit_events")


def test_edit_that_bypasses_the_guard_is_detected(log):
    log.append("dolores", "pa_specialist", "case.approve", "2026-1041")
    log.append("dolores", "pa_specialist", "case.approve", "2026-1036")
    conn = log._conn
    conn.execute("DROP TRIGGER audit_no_update")  # an attacker with DDL rights
    conn.execute("UPDATE audit_events SET actor = 'mallory' WHERE seq = 1")
    with pytest.raises(TamperDetected, match="event 1"):
        log.verify()
