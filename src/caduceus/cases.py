"""Cases: the unit of work a PA specialist reviews, and where they are in the workflow."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class State(StrEnum):
    NEW = "new"                        # received, not yet prepared by the agent
    REVIEW = "review"                  # prepared; with the PA specialist
    AWAITING_ATTESTATION = "awaiting_attestation"
    RETURNED = "returned"              # sent back to the ordering clinic for a correction
    SUBMITTED = "submitted"            # with the payer
    PENDED = "pended"                  # payer asked for more information
    AWAITING_NOTE = "awaiting_note"    # the payer asked for a note; with the ordering clinician to write it
    APPROVED = "approved"              # payer approved
    DENIED = "denied"                  # payer denied


class Attestation(BaseModel):
    criterion_id: str
    answer: str  # "yes" | "no"
    clinician_id: str
    clinician_name: str
    signed_at: datetime


class LastAction(BaseModel):
    action: str
    actor: str
    at: datetime
    undo_until: datetime
    prior: dict[str, Any]


class Case(BaseModel):
    id: str
    patient_id: str
    service: dict[str, str]
    criteria_set: str
    as_of: str
    payer: dict[str, str]
    ordering_clinician: dict[str, str]
    packet: list[str]
    received_at: datetime
    due_at: datetime
    state: State = State.NEW
    determination: dict[str, Any] | None = None
    checked_at: datetime | None = None
    draft: dict[str, Any] | None = None
    attestation_requested: list[str] = Field(default_factory=list)
    attestation_requested_at: datetime | None = None
    attestation_requested_by: str | None = None
    attestations: list[Attestation] = Field(default_factory=list)
    returned_reason: str | None = None
    submission: dict[str, Any] | None = None
    payer_response: dict[str, Any] | None = None
    # The answer to a payer's request for more: who asked the clinician, and the note they signed (or that they
    # could not document it). Sent to the payer only after a PA specialist approves it.
    reply: dict[str, Any] | None = None
    # What the payer's coverage-requirements service said at intake (CRD-style): whether prior authorization is
    # required, which policy version it publishes, and whether that matches the criteria approved here.
    requirements: dict[str, Any] | None = None
    sent_at: datetime | None = None       # when the request first reached the payer; starts the decision clock
    decided_at: datetime | None = None    # when the payer first approved or denied it
    # After a denial: the appeal the agent drafted, which a PA specialist reads (and may edit) before it is sent.
    appeal: dict[str, Any] | None = None
    last_action: LastAction | None = None
    version: int = 0

    def effective_results(self) -> list[dict[str, Any]]:
        """Engine results with clinician attestations applied on top. The engine's own output is unchanged."""
        signed = {a.criterion_id: a for a in self.attestations}
        out = []
        for r in (self.determination or {}).get("results", []):
            r = dict(r)
            a = signed.get(r["criterion_id"])
            if a and r["status"] != "met":
                r["engine_status"] = r["status"]
                r["status"] = "met" if a.answer == "yes" else "unmet"
                r["engine_explanation"] = r["explanation"]
                r["explanation"] = (f"Attested by {a.clinician_name}, the ordering clinician." if a.answer == "yes"
                                    else f"{a.clinician_name}, the ordering clinician, said this was not done.")
                r["attested_by"] = a.clinician_name
                r["attested_at"] = a.signed_at.isoformat()
            out.append(r)
        return out

    def effective_counts(self) -> dict[str, int]:
        counts = {"met": 0, "unmet": 0, "unknown": 0}
        for r in self.effective_results():
            counts[r["status"]] += 1
        return counts

    @property
    def sent_since_last_action(self) -> bool:
        """True once something the last action led to has reached the payer. That action can no longer be undone;
        an action taken after the payer already had the request (asking for a note, say) still can."""
        sub = self.submission or {}
        return bool(sub.get("sent")) and (self.last_action is None
                                          or sub != self.last_action.prior.get("submission"))

    @property
    def ready_to_approve(self) -> bool:
        c = self.effective_counts()
        return (self.state == State.REVIEW and c["unmet"] == 0 and c["unknown"] == 0 and sum(c.values()) > 0
                and not (self.draft or {}).get("out_of_date")
                and (self.requirements is None or bool(self.requirements.get("matches"))))


class Fax(BaseModel):
    """A faxed referral cover sheet waiting in the intake inbox, and what was read from it."""
    id: str
    file: str
    pages: list[str]                       # the referral's clinical pages that came with it
    received_at: datetime
    due_in_hours: float = 72
    as_of: str
    status: str = "received"               # received, read, confirmed
    reading: dict[str, Any] | None = None  # OCR fields with confidence, region and any reason to check
    case_id: str | None = None


class FaxRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.Lock()
        with self._lock:
            conn.execute("CREATE TABLE IF NOT EXISTS faxes (id TEXT PRIMARY KEY, data TEXT)")
            conn.commit()

    def save(self, fax: Fax) -> Fax:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO faxes(id, data) VALUES (?, ?)", (fax.id, fax.model_dump_json()))
            self._conn.commit()
        return fax

    def get(self, fax_id: str) -> Fax | None:
        with self._lock:
            row = self._conn.execute("SELECT data FROM faxes WHERE id = ?", (fax_id,)).fetchone()
        return Fax.model_validate(json.loads(row[0])) if row else None

    def all(self) -> list[Fax]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM faxes ORDER BY id").fetchall()
        return [Fax.model_validate(json.loads(r[0])) for r in rows]


class StaleCase(Exception):
    pass


class CaseRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.Lock()
        with self._lock:
            conn.execute("CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, state TEXT, data TEXT, version INT)")
            conn.commit()

    def save(self, case: Case) -> Case:
        with self._lock:
            row = self._conn.execute("SELECT version FROM cases WHERE id = ?", (case.id,)).fetchone()
            if row and row[0] != case.version:
                raise StaleCase(f"case {case.id} was changed by someone else; reload it")
            new = case.model_copy(update={"version": case.version + 1})
            self._conn.execute("INSERT OR REPLACE INTO cases(id, state, data, version) VALUES (?,?,?,?)",
                               (new.id, new.state.value, new.model_dump_json(), new.version))
            self._conn.commit()
            return new

    def get(self, case_id: str) -> Case | None:
        with self._lock:
            row = self._conn.execute("SELECT data FROM cases WHERE id = ?", (case_id,)).fetchone()
        return Case.model_validate(json.loads(row[0])) if row else None

    def all(self) -> list[Case]:
        with self._lock:
            rows = self._conn.execute("SELECT data FROM cases").fetchall()
        return [Case.model_validate(json.loads(r[0])) for r in rows]
