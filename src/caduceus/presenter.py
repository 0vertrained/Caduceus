"""Turns cases into what the screens show: due times in words, the status vocabulary, queue groups.

Every word here follows the design system's writing rules: due times are words, statuses are a fixed vocabulary,
and a disabled action always carries its reason.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from caduceus.audit.log import AuditEvent
from caduceus.auth import DEV_USERS
from caduceus.cases import Case, Fax, State
from caduceus.criteria.dsl import AllOf, AnyOf, ConditionRule, CriteriaSet
from caduceus.drafting import age_on, patient_name
from caduceus.fhir.record import codings, condition_is_active, condition_onset, display
from caduceus.fhir.store import FhirStore, document_text, fax_image
from caduceus.intake import fields_in_words
from caduceus.payer import EXPEDITED_DECISION_HOURS, RULE_PLANS, STANDARD_DECISION_DAYS
from caduceus.timeline import who
from caduceus.words import clock, long_date, moment, took
from caduceus.workflow import NOTE_LIMIT, REMIND_EVERY, UNDO_SECONDS, approve_blocked_reason

__all__ = ["Presenter", "clock", "due", "long_date", "moment", "status", "summary_sentence", "took"]

URGENT = timedelta(hours=2)
# How long a clinician's screen waits for the in-network model's summary. It takes about half a minute; a job lost to
# a restart never comes, and the screen must not say one is coming forever.
SUMMARY_PATIENCE = timedelta(minutes=5)


def summary_coming(summary: dict[str, Any], now: datetime) -> bool:
    """Whether the in-network model's summary is still on its way."""
    written = summary.get("generated_at")
    return bool(summary.get("pending")) and bool(written) and now - datetime.fromisoformat(written) < SUMMARY_PATIENCE


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
        State.DENIED: {"key": "denied", "word": "Denied; appeal drafted" if case.appeal else "Denied by the payer"},
    }[case.state]


ACTIVE = {State.REVIEW, State.PENDED, State.DENIED}


def sided(service: str, side: str | None) -> str:
    """The service with its side first, as a clinician says it: "Right total knee replacement"."""
    if not side:
        return service
    lowered = service[:1].lower() + service[1:] if service[1:2].islower() else service
    return f"{side.capitalize()} {lowered}"


def payer_clock(case: Case, now: datetime, tz: ZoneInfo) -> dict[str, Any] | None:
    """The payer's decision deadline under the CMS prior authorization rule, once the request has reached it."""
    plan = (case.requirements or {}).get("plan_type")
    if case.sent_at is None or plan is None:
        return None
    if plan not in RULE_PLANS:
        return {"applies": False, "plan_type": plan,
                "words": f"{plan} is not covered by the CMS prior authorization rule's decision deadlines."}
    expedited = case.urgency == "expedited"
    limit = timedelta(hours=EXPEDITED_DECISION_HOURS) if expedited else timedelta(days=STANDARD_DECISION_DAYS)
    named = f"{EXPEDITED_DECISION_HOURS}-hour" if expedited else f"{STANDARD_DECISION_DAYS}-day"
    due_at = case.sent_at + limit
    if case.decided_at is not None:
        within = case.decided_at <= due_at
        return {"applies": True, "plan_type": plan, "decided": True, "within": within, "expedited": expedited,
                "words": f"Decided in {took(case.decided_at - case.sent_at)}, "
                         f"{'within' if within else 'past'} the {named} limit."}
    return {"applies": True, "plan_type": plan, "decided": False, "within": now <= due_at, "expedited": expedited,
            "words": f"Due by {moment(due_at, now, tz)}: "
                     + (f"{EXPEDITED_DECISION_HOURS} hours for an expedited request" if expedited
                        else f"{STANDARD_DECISION_DAYS} calendar days") + " under the CMS rule."}
WITH_CLINICIAN = {State.AWAITING_ATTESTATION, State.AWAITING_NOTE}
WAITING = WITH_CLINICIAN | {State.RETURNED, State.SUBMITTED}
SEX = {"female": "Female", "male": "Male", "other": "Other", "unknown": "Unknown"}


def host(url: str | None) -> str:
    """An EHR named by its address's host, as a person would read it: "launch.smarthealthit.org"."""
    return (urlsplit(url).hostname or url) if url else "the EHR"


def lately(days: int | None) -> str:
    if days is None:
        return "the whole record"
    return "the last year" if days <= 366 else f"the last {round(days / 365)} years"


def diagnosis_codes(library: dict[str, CriteriaSet]) -> set[tuple[str, str]]:
    """Every diagnosis code an approved criterion looks for: what makes a diagnosis worth naming on the record page."""
    found: set[tuple[str, str]] = set()

    def walk(rule: Any) -> None:
        if isinstance(rule, AnyOf | AllOf):
            for r in rule.rules:
                walk(r)
        elif isinstance(rule, ConditionRule):
            found.update((c.uri, c.code) for c in rule.codes)

    for criteria in library.values():
        for c in criteria.criteria:
            walk(c.rule)
    return found


def answered(s: dict[str, Any]) -> str:
    """How much of a service's criteria the record answers: "The record answers 1 of the 4 criteria." """
    n, total = s["met"] + s["unmet"], s["total"]
    return (f"The record answers all {total} criteria." if n == total else
            f"The record answers none of the {total} criteria." if not n else
            f"The record answers {n} of the {total} criteria.")


def answers(s: dict[str, Any]) -> str:
    """That, and what follows from it: "The record answers 1 of the 4 criteria. Dr. Ana Reyes will be asked to confirm
    the other 3." """
    unmet, unknown = s["unmet"], s["unknown"]
    words = [answered(s)]
    if unmet:
        words.append(f"{'One is' if unmet == 1 else f'{unmet} are'} not met, so the request cannot be approved as "
                     "it stands.")
    if unknown:
        who = s.get("clinician") or "The ordering clinician"
        everything = unknown == s["total"]
        words.append(f"{who} will be asked to confirm {'all of them' if everything else f'the other {unknown}'}.")
    return " ".join(words)


class Presenter:
    def __init__(self, store: FhirStore, library: dict[str, CriteriaSet], tz: ZoneInfo):
        self.store, self.library, self.tz = store, library, tz
        self._patients: dict[str, dict] = {}

    def patient(self, case: Case) -> dict:
        if case.patient_id not in self._patients:
            self._patients[case.patient_id] = self.store.record(case.patient_id).patient
        return self._patients[case.patient_id]

    def forget(self, patient_id: str) -> None:
        """The record changed (the EHR shared it again): read the patient afresh next time."""
        self._patients.pop(patient_id, None)

    def row(self, case: Case, now: datetime) -> dict[str, Any]:
        p = self.patient(case)
        return {"id": case.id, "patient": {"name": patient_name(p), "age": age_on(p, date.fromisoformat(case.as_of))},
                "service": sided(case.service["short"], case.side),
                "payer": case.payer["name"], "due": due(case.due_at, now, self.tz), "status": status(case),
                "expedited": case.urgency == "expedited"}

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
                            "since": clock(since, self.tz),
                            "next": "Open it when the clinic resubmits." if c.state == State.RETURNED else None})
        return {
            "now": now.isoformat(),
            "counts": {"open": len(active), "due_today": len(groups["today"]), "approved_today": approved_today,
                       "with_clinicians": sum(c.state in WITH_CLINICIAN for c in cases)},
            "groups": [{"key": k, "title": {"today": "Due today", "tomorrow": "Due tomorrow", "later": "Later"}[k],
                        "cases": v} for k, v in groups.items()],
            "waiting": waiting,
        }

    def case(self, case: Case, now: datetime, viewer_id: str, read_only: bool = False) -> dict[str, Any]:
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
            leads = [] if r["status"] != "unknown" or r.get("attested_by") else self.leads(case, r["criterion_id"])
            results.append({"id": r["criterion_id"], "text": r["text"], "status": r["status"],
                            "explanation": r["explanation"], "attested_by": r.get("attested_by"),
                            "policy_sentence": r["policy_sentence"],
                            "question": crit[r["criterion_id"]].attestation_question, "evidence": evidence,
                            "leads": leads})
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
        actions = {"can_approve": case.ready_to_approve, "approve_blocked_reason": blocked,
                   "can_request_attestation": case.state == State.REVIEW and counts["unknown"] > 0,
                   "can_return": case.state in ACTIVE,
                   "can_ask_for_note": case.state == State.PENDED and not reply.get("note")
                   and not reply.get("declined_by"),
                   "can_send_note": case.state == State.PENDED and bool(reply.get("note")),
                   "can_send_appeal": case.state == State.DENIED and bool(case.appeal),
                   "can_edit_appeal": case.state == State.DENIED and bool(case.appeal),
                   # Approved but not sent, and this person cannot undo it (their 30 seconds passed, or it was
                   # someone else's approval): it can still be recalled until the outbox sends it.
                   "can_recall": case.state == State.SUBMITTED and not (case.submission or {}).get("sent")
                   and undo is None,
                   "can_resubmit": case.state == State.RETURNED,
                   # Until it is approved: the request goes to the payer marked expedited, or not.
                   "can_set_urgency": case.state in (State.REVIEW, State.AWAITING_ATTESTATION, State.RETURNED)}
        waiting = self.clinician_wait(case, now)
        actions["can_remind"] = bool(waiting and waiting["next_reminder_at"] is None)
        return {
            "id": case.id, "version": case.version, "state": case.state.value, "status": status(case),
            "patient": {"name": patient_name(p), "birth_date": long_date(born),
                        "age": age_on(p, date.fromisoformat(case.as_of))},
            "service": case.service, "payer": case.payer, "ordering_clinician": case.ordering_clinician,
            "received": long_date(case.received_at.astimezone(self.tz).date()), "due": due(case.due_at, now, self.tz),
            "packet": packet,
            "criteria": {"title": cset.title, "version": cset.version, "source_kind": cset.source.kind,
                         "source": cset.source.document and {
                             "document": cset.source.document, "title": cset.source.title, "url": cset.source.url,
                             "retrieved": long_date(cset.source.retrieved) if cset.source.retrieved else None},
                         "checked_at": clock(case.checked_at, self.tz) if case.checked_at else None,
                         "counts": counts, "total": total, "results": results},
            "draft": case.draft,
            "summary": summary_sentence(case, counts, total),
            "actions": actions,
            "undo": undo,
            "waiting": waiting,
            "returned_reason": case.returned_reason,
            "payer_response": case.payer_response,
            "submission": case.submission,
            "reply": self.reply(case, now),
            "appeal": self.appeal(case),
            "payer_clock": payer_clock(case, now, self.tz),
            "side": case.side, "urgency": case.urgency, "urgency_reason": case.urgency_reason,
            "payer_review": self.payer_review(case, now),
            "requirements": case.requirements and {
                "matches": case.requirements.get("matches"), "plan_type": case.requirements.get("plan_type"),
                "checked_at": clock(datetime.fromisoformat(case.requirements["checked_at"]), self.tz)},
            "read_only": read_only,
        } | ({"actions": {k: False if k.startswith("can_") else v for k, v in actions.items()},
              "summary": "Read only. Only a prior-authorization specialist can act on a case."}
             if read_only else {})

    def leads(self, case: Case, criterion_id: str) -> list[dict[str, Any]]:
        """Where the packet may answer a criterion the record could not: each passage, its page and where on it."""
        return [{"document_id": lead["doc_id"], "page": lead["page"], "text": lead["text"],
                 "start": lead["start"], "end": lead["end"],
                 "title": (self.store.document(lead["doc_id"]) or {}).get("description", "Page")}
                for lead in (case.leads or {}).get(criterion_id, [])]

    def payer_review(self, case: Case, now: datetime) -> dict[str, Any] | None:
        """While the payer is reviewing: when it said it would decide, in words."""
        waiting = case.payer_response or {}
        if case.state != State.SUBMITTED or not waiting.get("in_review"):
            return None
        at = datetime.fromisoformat(waiting["expected_by"])
        # Said after "Sent to <payer>.", so it names the payer as "it".
        return {"expected_by": at.isoformat(),
                "words": f"Its decision is expected by {moment(at, now, self.tz)}." if at > now
                else "Its decision is due now."}

    def clinician_wait(self, case: Case, now: datetime) -> dict[str, Any] | None:
        """How long the case has waited on the ordering clinician, and the reminders sent, in words."""
        since = case.waiting_since()
        if since is None:
            return None
        sent = case.reminders_since_asked()
        last = datetime.fromisoformat(sent[-1]["at"]) if sent else None
        next_at = last + REMIND_EVERY if last and now - last < REMIND_EVERY else None
        words = f"Waiting {took(now - since)} so far."
        if last:
            words += (f" Reminded at {clock(last, self.tz)}." if len(sent) == 1
                      else f" Reminded {len(sent)} times, last at {clock(last, self.tz)}.")
        if next_at:
            words += f" You can send another reminder at {clock(next_at, self.tz)}."
        return {"since": moment(since, now, self.tz), "for": took(now - since), "reminders": len(sent),
                "last_reminded": clock(last, self.tz) if last else None,
                "next_reminder_at": clock(next_at, self.tz) if next_at else None, "words": words}

    def appeal(self, case: Case) -> dict[str, Any] | None:
        """A denial with its specific reason, the policy and record behind the criterion, and the drafted appeal."""
        decision = (case.payer_response or {}).get("decision", {})
        if not case.appeal and decision.get("code") != "A3":
            return None
        reason = decision.get("reason") or (case.appeal or {}).get("reason") or {}
        result = next((r for r in case.effective_results() if r["text"] == reason.get("criterion")), None)
        return {"reason": reason.get("text"), "criterion": reason.get("criterion"),
                "policy_sentence": result and result["policy_sentence"], "record": result and result["explanation"],
                "draft": case.appeal}

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
                "author": (doc.get("author") or [{}])[0].get("display"), "text": document_text(doc),
                # A faxed page keeps its image: the text beside it is only what OCR read.
                "image": f"/api/cases/{case.id}/pages/{doc_id}/image" if fax_image(doc) else None}

    def came_by_fax(self, fax: Fax | None, events: list[AuditEvent], clinic: str | None,
                    now: datetime) -> dict[str, Any] | None:
        """How a case created from a fax came to be, read from the fax's audit events: who checked it against the
        page, when, what they corrected, and whether the clinic was asked for anything first."""
        confirmed = next((e for e in reversed(events) if e.action == "fax.confirmed"), None)
        if fax is None or confirmed is None:
            return None
        corrected = fields_in_words(confirmed.detail.get("corrected", []))
        asked = [e for e in events if e.action == "fax.clinic_asked"]
        words = f"Came by fax from {clinic}." if clinic else "Came by fax."
        words += f" {who(confirmed)} checked it against the page {moment(confirmed.ts, now, self.tz)}"
        words += f" and corrected {corrected}." if corrected else "; nothing needed correcting."
        if asked:
            last = asked[-1].detail
            words += (" Before that, the clinic was asked to fax it again." if last.get("kind") == "resend"
                      else f" Before that, the clinic was asked for {fields_in_words(last.get('missing', []))}.")
        return {"from": clinic, "by": who(confirmed), "at": moment(confirmed.ts, now, self.tz),
                "corrected": corrected or None, "asked_clinic": bool(asked), "words": words}

    def came_from_ehr(self, case: Case, events: list[AuditEvent], record: dict[str, Any] | None,
                      now: datetime) -> dict[str, Any] | None:
        """How a case started from inside an EHR came to be: from which EHR, who started it and when."""
        started = next((e for e in events if e.action == "case.received"
                        and e.detail.get("source") == "smart_launch"), None)
        if started is None:
            return None
        where, at = host(started.detail.get("iss")), moment(case.received_at, now, self.tz)
        words = f"Opened from the EHR at {where} by a SMART launch. {who(started)} started the case {at}"
        words += f", from the record the EHR shared {moment(record['read_at'], now, self.tz)}." if record else "."
        return {"from": where, "by": who(started), "at": at, "words": words}

    def ehr_record(self, data: dict[str, Any], preview: list[dict[str, Any]], cases: list[Case],
                   now: datetime) -> dict[str, Any]:
        """A record an EHR shared, before a case is started from it: the patient, what the record holds in plain
        words (with the diagnoses the criteria look for named), and how much of each service's criteria it answers."""
        p, today = data["patient"], now.astimezone(self.tz).date()
        as_of = date.fromisoformat(data["as_of"])
        conditions = [r for r in data["resources"] if r.get("resourceType") == "Condition"]
        active = [r for r in conditions if condition_is_active(r, as_of)]
        wanted = diagnosis_codes(self.library)
        relevant = []
        for r in sorted(active, key=lambda r: condition_onset(r) or as_of):
            if wanted & set(codings(r)):
                since = condition_onset(r)
                relevant.append(f"{display(r)}, on the record since {long_date(since)}" if since else display(r))
        counts, windows, held_back = data["counts"], data["windows"], set(data["not_shared"])

        def row(what: str, kind: str, words: str) -> dict[str, str]:
            return {"what": what, "words": "The EHR did not share these." if kind in held_back else words}

        n = {k: counts.get(k, 0) for k in ("Condition", "Procedure", "Observation")}
        notes = len(data["notes"])
        holds = [
            row("Diagnoses", "Condition", f"{n['Condition']} on the record, {len(active)} of them active."
                if n["Condition"] else "None on the record."),
            row("Procedures", "Procedure", f"{n['Procedure'] or 'None'} in {lately(windows.get('Procedure'))}."),
            row("Measurements and test results", "Observation",
                f"{n['Observation'] or 'None'} in {lately(windows.get('Observation'))}."),
            row("Notes", "DocumentReference", f"The {notes} newest, which become the case’s pages." if notes > 1
                else "The newest, which becomes the case’s page." if notes else "None the case can show as a page."),
        ]
        by = DEV_USERS.get(data.get("read_by", ""))
        return {
            "key": data["key"],
            "patient": {"name": patient_name(p), "born": long_date(date.fromisoformat(p["birthDate"])),
                        "age": age_on(p, today), "sex": SEX.get(p.get("gender", ""), "Not recorded")},
            "source": {"host": host(data["iss"]), "read": moment(data["read_at"], now, self.tz),
                       "by": by.name if by else data.get("read_by")},
            "holds": holds, "relevant": relevant,
            "reads": ("Caduceus asked the EHR only for what the coverage criteria can use: every diagnosis, procedures "
                      f"from {lately(windows.get('Procedure'))}, measurements and test results from "
                      f"{lately(windows.get('Observation'))}, and the newest notes."),
            "services": [s | {"lead": answered(s), "answers": answers(s)} for s in preview],
            "cases": [{"id": c.id, "service": c.service["short"], "status": status(c)}
                      for c in sorted(cases, key=lambda c: c.id)],
        }

    def attestation(self, case: Case, summary: dict[str, Any], now: datetime) -> dict[str, Any]:
        p = self.patient(case)
        crit = {c.id: c for c in self.library[case.criteria_set].criteria}
        out = {
            "case_id": case.id, "state": case.state.value,
            "patient": {"name": patient_name(p), "birth_date": long_date(date.fromisoformat(p["birthDate"]))},
            "service": case.service["display"], "ordered": long_date(case.received_at.astimezone(self.tz).date()),
            "chart_summary": summary["text"],
            # How it was written: by the model inside the network, or from the record's facts.
            "chart_summary_by": {"route": summary.get("route"), "model": summary.get("model")},
            "chart_summary_pending": summary_coming(summary, now),  # the in-network model's is on its way
            "questions": [{"criterion_id": cid, "question": crit[cid].attestation_question or crit[cid].text,
                           "help": crit[cid].attestation_help,
                           "leads": [{k: lead[k] for k in ("title", "page", "text")} for lead in self.leads(case, cid)]}
                          for cid in case.attestation_requested],
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
    # Numerals from 2 up, as the screens write them (GOV.UK style): "3 of 4 criteria are met."
    met = {0: "No", 1: "One"}.get(counts["met"], str(counts["met"]))
    all_met = counts["met"] == total and total > 1
    lead = f"All {total} criteria are met." if all_met else f"{met} of {total} criteria are met."
    clin, payer = case.ordering_clinician["name"], case.payer["name"]
    if case.state == State.AWAITING_ATTESTATION:
        return f"{lead} Waiting for {clin} to sign."
    if case.state == State.AWAITING_NOTE:
        return f"Waiting for {clin} to write the note {payer} asked for."
    if case.state == State.DENIED:
        reason = (case.payer_response or {}).get("decision", {}).get("reason", {}).get("text", "")
        return (f"{payer} denied it. {reason} An appeal citing the policy and the record is drafted; read it, "
                "then send it.") if case.appeal else f"{payer} denied it. {reason}"
    if case.state == State.PENDED:
        reply = case.reply or {}
        if reply.get("note"):
            return f"{clin} signed the note. Read it, then send it to {payer}."
        if reply.get("declined_by"):
            return f"{clin} cannot document this, so there is no note to send. Return the case to the clinic."
        return f"{payer} needs a note from {clin} before it decides."
    if case.state == State.SUBMITTED and (case.payer_response or {}).get("in_review"):
        return f"{lead} Sent to {payer}, which is reviewing it."
    if case.state != State.REVIEW:
        return lead
    reason = approve_blocked_reason(case)
    if reason and counts["unknown"]:
        return f"{lead} {reason.rstrip('.')} before you can approve."
    return (
        f"{lead} {reason}" if reason else f"{lead} This request is ready to approve.")
