"""Turns cases into what the screens show: due times in words, the status vocabulary, queue groups.

Every word here follows the design system's writing rules: due times are words, statuses are a fixed vocabulary,
and a disabled action always carries its reason.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from caduceus.cases import Case, State
from caduceus.criteria.dsl import CriteriaSet
from caduceus.drafting import age_on, patient_name
from caduceus.fhir.store import FhirStore, document_text
from caduceus.workflow import NOTE_LIMIT, UNDO_SECONDS, approve_blocked_reason

URGENT = timedelta(hours=2)


def clock(dt: datetime, tz: ZoneInfo) -> str:
    t = dt.astimezone(tz)
    if t.hour == 12 and t.minute == 0:
        return "noon"
    return f"{t.hour % 12 or 12}:{t.minute:02d} {'am' if t.hour < 12 else 'pm'}"


def long_date(d: date) -> str:
    return f"{d:%B} {d.day}, {d.year}"


def moment(dt: datetime | str, now: datetime, tz: ZoneInfo) -> str:
    """'today at 1:12 pm', or 'October 28, 2026 at 9:05 am' for another day."""
    dt = datetime.fromisoformat(dt) if isinstance(dt, str) else dt
    day = dt.astimezone(tz).date()
    return f"{'today' if day == now.astimezone(tz).date() else long_date(day)} at {clock(dt, tz)}"


def due(due_at: datetime, now: datetime, tz: ZoneInfo) -> dict[str, Any]:
    local_due, local_now = due_at.astimezone(tz), now.astimezone(tz)
    delta = due_at - now
    days = (local_due.date() - local_now.date()).days
    if delta.total_seconds() < 0:
        mins = int(-delta.total_seconds() // 60)
        words = f"Overdue by {mins // 60} h {mins % 60} min" if mins >= 60 else f"Overdue by {mins} minutes"
        return {"words": words, "sub": f"was due at {clock(due_at, tz)}", "urgent": True, "group": "today"}
    if days == 0:
        mins = int(delta.total_seconds() // 60)
        h, m = divmod(mins, 60)
        if h == 0:
            words = f"In {m} minutes"
        elif m < 10 or h >= 3:
            words = f"In {h} hour{'s' if h != 1 else ''}"
        else:
            words = f"In {h} hour{'s' if h != 1 else ''} {m} minutes"
        return {"words": words, "sub": f"due at {clock(due_at, tz)}", "urgent": delta <= URGENT, "group": "today"}
    group = "tomorrow" if days == 1 else "later"
    words = "Tomorrow" if days == 1 else f"{local_due:%A}" if days < 7 else long_date(local_due.date())
    return {"words": words, "sub": f"due at {clock(due_at, tz)}", "urgent": False, "group": group}


def status(case: Case) -> dict[str, str]:
    clin = case.ordering_clinician["name"]
    if case.state == State.REVIEW:
        c = case.effective_counts()
        if c["unmet"]:
            return {"key": "not_met", "word": "Criteria not met"}
        if c["unknown"]:
            return {"key": "needs_clinician", "word": "Needs a clinician"}
        return {"key": "ready", "word": "Ready to approve"}
    sub = case.submission or {}
    return {
        State.NEW: {"key": "preparing", "word": "Being prepared"},
        State.AWAITING_ATTESTATION: {"key": "waiting_clinician", "word": f"Waiting on {clin}"},
        State.RETURNED: {"key": "returned", "word": f"Returned to {clin}"},
        State.SUBMITTED: ({"key": "waiting_payer", "word": "Waiting on the payer"} if sub.get("sent")
                          else {"key": "sending", "word": "Sending to the payer"}),
        State.PENDED: ({"key": "ready", "word": "Ready to approve"} if (case.reply or {}).get("note")
                       else {"key": "payer_question", "word": "The payer asked a question"}),
        State.AWAITING_NOTE: {"key": "waiting_clinician", "word": f"Waiting on {clin}"},
        State.APPROVED: {"key": "approved", "word": "Approved by the payer"},
        State.DENIED: {"key": "denied", "word": "Denied by the payer"},
    }[case.state]


ACTIVE = {State.REVIEW, State.PENDED}
WITH_CLINICIAN = {State.AWAITING_ATTESTATION, State.AWAITING_NOTE}
WAITING = WITH_CLINICIAN | {State.RETURNED, State.SUBMITTED}


class Presenter:
    def __init__(self, store: FhirStore, library: dict[str, CriteriaSet], tz: ZoneInfo):
        self.store, self.library, self.tz = store, library, tz
        self._patients: dict[str, dict] = {}

    def patient(self, case: Case) -> dict:
        if case.patient_id not in self._patients:
            self._patients[case.patient_id] = self.store.record(case.patient_id).patient
        return self._patients[case.patient_id]

    def row(self, case: Case, now: datetime) -> dict[str, Any]:
        p = self.patient(case)
        return {"id": case.id, "patient": {"name": patient_name(p), "age": age_on(p, date.fromisoformat(case.as_of))},
                "service": case.service["short"], "payer": case.payer["name"], "due": due(case.due_at, now, self.tz),
                "status": status(case)}

    def queue(self, cases: list[Case], now: datetime, approved_today: int) -> dict[str, Any]:
        active = sorted((c for c in cases if c.state in ACTIVE), key=lambda c: c.due_at)
        groups = {"today": [], "tomorrow": [], "later": []}
        for c in active:
            r = self.row(c, now)
            groups[r["due"]["group"]].append(r)
        waiting = []
        for c in sorted((c for c in cases if c.state in WAITING), key=lambda c: c.id):
            since = (c.last_action.at if c.last_action else c.attestation_requested_at) or c.received_at
            waiting.append({"id": c.id, "patient": patient_name(self.patient(c)), "status": status(c),
                            "since": clock(since, self.tz)})
        return {
            "now": now.isoformat(),
            "counts": {"open": len(active), "due_today": len(groups["today"]), "approved_today": approved_today,
                       "with_clinicians": sum(c.state in WITH_CLINICIAN for c in cases)},
            "groups": [{"key": k, "title": {"today": "Due today", "tomorrow": "Due tomorrow", "later": "Later"}[k],
                        "cases": v} for k, v in groups.items()],
            "waiting": waiting,
        }

    def case(self, case: Case, now: datetime, viewer_id: str) -> dict[str, Any]:
        p = self.patient(case)
        born = date.fromisoformat(p["birthDate"])
        cset = self.library[case.criteria_set]
        crit = {c.id: c for c in cset.criteria}
        pages = {doc_id: i for i, doc_id in enumerate(case.packet, start=1)}
        results = []
        for r in case.effective_results():
            evidence = []
            for e in r.get("evidence", []):
                doc_id = (e.get("source_document") or "").split("/")[-1] or None
                evidence.append({"display": e["display"], "date": e["date"], "document_id": doc_id,
                                 "page": pages.get(doc_id) if doc_id else None, "sentence": e.get("source_sentence")})
            results.append({"id": r["criterion_id"], "text": r["text"], "status": r["status"],
                            "explanation": r["explanation"], "attested_by": r.get("attested_by"),
                            "policy_sentence": r["policy_sentence"],
                            "question": crit[r["criterion_id"]].attestation_question, "evidence": evidence})
        counts = case.effective_counts()
        packet = []
        for doc_id in case.packet:
            doc = self.store.document(doc_id) or {}
            packet.append({"id": doc_id, "page": pages[doc_id], "title": doc.get("description", "Page")})
        last = case.last_action
        undo = None
        if last and last.actor == viewer_id and now <= last.undo_until and not case.sent_since_last_action:
            undo = {"action": last.action, "until": last.undo_until.isoformat(), "seconds": UNDO_SECONDS}
        total = sum(counts.values())
        blocked = approve_blocked_reason(case)
        reply = case.reply or {}
        return {
            "id": case.id, "state": case.state.value, "status": status(case),
            "patient": {"name": patient_name(p), "birth_date": long_date(born),
                        "age": age_on(p, date.fromisoformat(case.as_of))},
            "service": case.service, "payer": case.payer, "ordering_clinician": case.ordering_clinician,
            "received": long_date(case.received_at.astimezone(self.tz).date()), "due": due(case.due_at, now, self.tz),
            "packet": packet,
            "criteria": {"title": cset.title, "version": cset.version, "source_kind": cset.source.kind,
                         "checked_at": clock(case.checked_at, self.tz) if case.checked_at else None,
                         "counts": counts, "total": total, "results": results},
            "draft": case.draft,
            "summary": summary_sentence(case, counts, total),
            "actions": {"can_approve": case.ready_to_approve, "approve_blocked_reason": blocked,
                        "can_request_attestation": case.state == State.REVIEW and counts["unknown"] > 0,
                        "can_return": case.state in ACTIVE,
                        "can_ask_for_note": case.state == State.PENDED and not reply.get("note")
                        and not reply.get("declined_by"),
                        "can_send_note": case.state == State.PENDED and bool(reply.get("note"))},
            "undo": undo,
            "returned_reason": case.returned_reason,
            "payer_response": case.payer_response,
            "submission": case.submission,
            "reply": self.reply(case, now),
        }

    def reply(self, case: Case, now: datetime) -> dict[str, Any] | None:
        """The payer's request for more, and where its answer stands, in words."""
        if case.state not in (State.PENDED, State.AWAITING_NOTE) and not case.reply:
            return None
        reply = case.reply or {}
        attested = [{"text": r["text"], "by": r["attested_by"], "at": moment(r["attested_at"], now, self.tz)}
                    for r in case.effective_results() if r.get("attested_by") and r["status"] == "met"]
        note = reply.get("note")
        return {
            "question": reply.get("question") or (case.payer_response or {}).get("decision", {}).get("question"),
            "attested": attested,
            "requested_by": reply.get("requested_by"),
            "note": note and {"text": note["text"], "author": note["author"],
                              "signed_at": moment(note["signed_at"], now, self.tz)},
            "declined": reply.get("declined_by") and {"by": reply["declined_by"],
                                                      "at": moment(reply["declined_at"], now, self.tz)},
        }

    def page(self, doc_id: str, case: Case) -> dict[str, Any]:
        if doc_id not in case.packet:
            raise KeyError(doc_id)
        doc = self.store.document(doc_id) or {}
        when = doc.get("date", "")[:10]
        return {"id": doc_id, "title": doc.get("description", "Page"), "page": case.packet.index(doc_id) + 1,
                "pages": len(case.packet), "date": long_date(date.fromisoformat(when)) if when else None,
                "author": (doc.get("author") or [{}])[0].get("display"), "text": document_text(doc)}

    def attestation(self, case: Case, summary: str, now: datetime) -> dict[str, Any]:
        p = self.patient(case)
        crit = {c.id: c for c in self.library[case.criteria_set].criteria}
        out = {
            "case_id": case.id, "state": case.state.value,
            "patient": {"name": patient_name(p), "birth_date": long_date(date.fromisoformat(p["birthDate"]))},
            "service": case.service["display"], "ordered": long_date(case.received_at.astimezone(self.tz).date()),
            "chart_summary": summary,
            "questions": [{"criterion_id": cid, "question": crit[cid].attestation_question or crit[cid].text,
                           "help": crit[cid].attestation_help} for cid in case.attestation_requested],
            "payer": case.payer["name"],
            "requested_by": case.attestation_requested_by or "The prior authorization team",
            "note_request": None,
        }
        if case.reply:
            signed = {a.criterion_id: a for a in case.attestations if a.answer == "yes"}
            out["requested_by"] = case.reply.get("requested_by") or out["requested_by"]
            out["note_request"] = {
                "question": case.reply.get("question"),
                "confirmed": [{"question": crit[c].attestation_question or crit[c].text,
                               "policy": crit[c].policy_sentence, "at": moment(signed[c].signed_at, now, self.tz)}
                              for c in case.reply.get("criteria", []) if c in signed],
                "limit": NOTE_LIMIT,
            }
        return out


def summary_sentence(case: Case, counts: dict[str, int], total: int) -> str:
    words = {1: "One", 2: "Two", 3: "Three", 4: "Four", 5: "Five"}
    met = words.get(counts["met"], str(counts["met"]))
    lead = f"{met} of {words.get(total, str(total)).lower()} criteria are met."
    clin, payer = case.ordering_clinician["name"], case.payer["name"]
    if case.state == State.AWAITING_ATTESTATION:
        return f"{lead} Waiting for {clin} to sign."
    if case.state == State.AWAITING_NOTE:
        return f"Waiting for {clin} to write the note {payer} asked for."
    if case.state == State.PENDED:
        reply = case.reply or {}
        if reply.get("note"):
            return f"{clin} signed the note. Read it, then send it to {payer}."
        if reply.get("declined_by"):
            return f"{clin} cannot document this, so there is no note to send. Return the case to the clinic."
        return f"{payer} needs a note from {clin} before it decides."
    if case.state != State.REVIEW:
        return lead
    reason = approve_blocked_reason(case)
    if reason and counts["unknown"]:
        return f"{lead} {reason.rstrip('.')} before you can approve."
    return (
        f"{lead} {reason}" if reason else f"{lead} This request is ready to approve.")
