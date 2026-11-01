"""Cases: the unit of work a PA specialist reviews, and where they are in the workflow."""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import threading
import zlib
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


class State(StrEnum):
    NEW = "new"                        # received, not yet prepared by the agent
    REVIEW = "review"                  # with the PA specialist
    AWAITING_ATTESTATION = "awaiting_attestation"
    RETURNED = "returned"              # sent back to the ordering clinic for a correction
    SUBMITTED = "submitted"            # approved: in the outbox, then with the payer
    PENDED = "pended"                  # the payer asked for more information
    AWAITING_NOTE = "awaiting_note"    # with the ordering clinician, to write the note the payer asked for
    APPROVED = "approved"
    DENIED = "denied"


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
    side: Literal["right", "left"] | None = None
    # Expedited when the standard timeframe could seriously jeopardize the patient (CMS-0057-F): 72 hours, not 7 days.
    urgency: Literal["standard", "expedited"] = "standard"
    urgency_reason: str | None = None
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
    # Packet passages that may answer each criterion the record could not: for a person to read, never evidence.
    leads: dict[str, list[dict[str, Any]]] | None = None
    # What the chart shows, for the clinician asked to attest, with the route and model that wrote it.
    chart_summary: dict[str, Any] | None = None
    submission: dict[str, Any] | None = None
    payer_response: dict[str, Any] | None = None
    # The payer's question, and the note the clinician signed for it (or that they could not document it).
    reply: dict[str, Any] | None = None
    # The payer's coverage-requirements answer at intake (CRD-style), and whether its policy matches the approved one.
    requirements: dict[str, Any] | None = None
    sent_at: datetime | None = None       # when the request first reached the payer; starts the decision clock
    decided_at: datetime | None = None    # the payer's first approval or denial
    appeal: dict[str, Any] | None = None
    last_action: LastAction | None = None
    reminders: list[dict[str, Any]] = Field(default_factory=list)
    version: int = 0

    def waiting_since(self) -> datetime | None:
        """When the ordering clinician was asked for what the case now waits on (a signature or a note), if it does."""
        if self.state == State.AWAITING_ATTESTATION:
            return self.attestation_requested_at
        reply = self.reply or {}
        if self.state == State.AWAITING_NOTE and reply.get("requested_at"):
            return datetime.fromisoformat(reply["requested_at"])
        return None

    def reminders_since_asked(self) -> list[dict[str, Any]]:
        """The reminders about what the case waits on now; earlier requests' reminders do not count."""
        since = self.waiting_since()
        return [r for r in self.reminders if since and datetime.fromisoformat(r["at"]) >= since]

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
        """True once something the last action led to has reached the payer, so it can no longer be undone. An action
        taken after the payer already had the request, such as asking for a note, still can be."""
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
    status: str = "received"               # received, read, waiting_on_clinic, confirmed
    reading: dict[str, Any] | None = None  # OCR fields with confidence, region and any reason to check
    case_id: str | None = None
    request: dict[str, Any] | None = None  # what a person asked the sending clinic for, and when it answered


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


class DocumentRepository:
    """Documents Caduceus files itself, such as a faxed cover sheet, shaped like the record's DocumentReferences so the
    tools and screens read them alike (caduceus.fhir.store.WithFiledDocuments)."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.Lock()
        with self._lock:
            conn.execute("CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, data TEXT)")
            conn.commit()

    def save(self, doc: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO documents(id, data) VALUES (?, ?)", (doc["id"], json.dumps(doc)))
            self._conn.commit()

    def get(self, doc_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT data FROM documents WHERE id = ?", (doc_id,)).fetchone()
        return json.loads(row[0]) if row else None


# A record from an EHR is named by a random key, never by the EHR's id for the patient: the key appears in addresses
# and so in access logs. Its notes are the pages "<key>-1", "<key>-2" and so on.
EHR_RECORD = re.compile(r"ehr-[0-9a-f]{20}")


def new_ehr_key() -> str:
    return f"ehr-{secrets.token_hex(10)}"


class EhrRecordRepository:
    """Patients' records read from an EHR by a SMART launch, kept with the cases and stored compressed. `match` hashes
    the EHR and its id for the patient, so a new read of the same patient finds the earlier one."""

    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn
        self._lock = threading.Lock()
        with self._lock:
            conn.execute("CREATE TABLE IF NOT EXISTS ehr_records (key TEXT PRIMARY KEY, match TEXT UNIQUE, "
                         "saved_at TEXT, data BLOB)")
            conn.commit()

    def save(self, key: str, match: str, data: dict[str, Any]) -> None:
        blob = zlib.compress(json.dumps(data, separators=(",", ":")).encode())
        with self._lock:
            self._conn.execute("INSERT OR REPLACE INTO ehr_records(key, match, saved_at, data) VALUES (?, ?, ?, ?)",
                               (key, match, datetime.now(UTC).isoformat(), blob))
            self._conn.commit()

    def get(self, key: str) -> dict[str, Any] | None:
        if not EHR_RECORD.fullmatch(key):
            return None
        with self._lock:
            row = self._conn.execute("SELECT data FROM ehr_records WHERE key = ?", (key,)).fetchone()
        return json.loads(zlib.decompress(row[0])) if row else None

    def find(self, match: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT key FROM ehr_records WHERE match = ?", (match,)).fetchone()
        return row[0] if row else None

    def retire(self, key: str) -> None:
        """Keep a record for the cases made from it, but stop finding it by `match`, so a new read of the patient is
        filed apart and a case never reads a record that changed under it."""
        with self._lock:
            self._conn.execute("UPDATE ehr_records SET match = NULL WHERE key = ?", (key,))
            self._conn.commit()

    def keys_oldest_first(self) -> list[str]:
        with self._lock:
            return [r[0] for r in self._conn.execute("SELECT key FROM ehr_records ORDER BY saved_at, key").fetchall()]

    def delete(self, key: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM ehr_records WHERE key = ?", (key,))
            self._conn.commit()


class StaleCase(Exception):
    """The case was saved by someone else after it was read."""


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
