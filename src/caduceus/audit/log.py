"""Append-only, hash-chained audit log.

Every state transition, tool call, model call, determination and human action is recorded with the actor,
their role, the case, and hashes of inputs and outputs. The database itself refuses UPDATE and DELETE (SQLite
triggers here; REVOKE on the Postgres role in deployment, see infra/sql/audit.sql), and each row carries the hash
of the previous row, so any later edit is detectable by `verify()`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_events (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    role       TEXT NOT NULL,
    case_id    TEXT,
    action     TEXT NOT NULL,
    detail     TEXT NOT NULL,
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_case ON audit_events(case_id, seq);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
"""

GENESIS = "0" * 64


def sha256_of(value: object) -> str:
    data = value if isinstance(value, bytes) else json.dumps(value, sort_keys=True, default=str).encode()
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class AuditEvent:
    seq: int
    ts: str
    actor: str
    role: str
    case_id: str | None
    action: str
    detail: dict
    prev_hash: str
    hash: str


class TamperDetected(Exception):
    pass


class AuditLog:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.Lock()
        with self._lock:
            conn.executescript(SCHEMA)
            conn.commit()

    @staticmethod
    def _row_hash(prev: str, ts: str, actor: str, role: str, case_id: str | None, action: str, detail: str) -> str:
        return sha256_of({"prev": prev, "ts": ts, "actor": actor, "role": role, "case": case_id,
                          "action": action, "detail": detail})

    def append(self, actor: str, role: str, action: str, case_id: str | None = None,
               detail: dict | None = None) -> AuditEvent:
        detail_json = json.dumps(detail or {}, sort_keys=True, default=str)
        with self._lock:
            row = self._conn.execute("SELECT hash FROM audit_events ORDER BY seq DESC LIMIT 1").fetchone()
            prev = row[0] if row else GENESIS
            ts = datetime.now(UTC).isoformat(timespec="microseconds")
            h = self._row_hash(prev, ts, actor, role, case_id, action, detail_json)
            cur = self._conn.execute(
                "INSERT INTO audit_events(ts, actor, role, case_id, action, detail, prev_hash, hash) "
                "VALUES (?,?,?,?,?,?,?,?)", (ts, actor, role, case_id, action, detail_json, prev, h))
            self._conn.commit()
            return AuditEvent(cur.lastrowid, ts, actor, role, case_id, action, json.loads(detail_json), prev, h)

    def events(self, case_id: str | None = None) -> list[AuditEvent]:
        q = "SELECT seq, ts, actor, role, case_id, action, detail, prev_hash, hash FROM audit_events"
        args: tuple = ()
        if case_id is not None:
            q += " WHERE case_id = ?"
            args = (case_id,)
        with self._lock:
            rows = self._conn.execute(q + " ORDER BY seq", args).fetchall()
        return [AuditEvent(r[0], r[1], r[2], r[3], r[4], r[5], json.loads(r[6]), r[7], r[8]) for r in rows]

    def verify(self) -> int:
        """Re-walk the chain; returns the number of events verified or raises TamperDetected."""
        prev = GENESIS
        events = self.events()
        for e in events:
            detail_json = json.dumps(e.detail, sort_keys=True, default=str)
            if e.prev_hash != prev or e.hash != self._row_hash(prev, e.ts, e.actor, e.role, e.case_id,
                                                              e.action, detail_json):
                raise TamperDetected(f"audit chain broken at event {e.seq}")
            prev = e.hash
        return len(events)
