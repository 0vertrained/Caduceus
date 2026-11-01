"""The case workflow: what the agent does, what people do, and the rules between them.

Agent steps run as the agent-runtime identity through the tool gateway; people's steps are checked here. Anything
for the payer waits in an outbox for the undo window before the agent sends it (ADR 4).
"""

from __future__ import annotations

import functools
import re
import threading
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Literal

from caduceus.audit.log import sha256_of
from caduceus.auth import AGENT, Principal, Role
from caduceus.cases import Attestation, Case, EhrRecordRepository, Fax, LastAction, State, new_ehr_key
from caduceus.criteria.dsl import CriteriaSet
from caduceus.criteria.engine import Engine
from caduceus.drafting import Drafter, age_on, mid_sentence, patient_context, patient_name
from caduceus.fhir.record import PatientRecord
from caduceus.fhir.store import FhirStore
from caduceus.intake import FORM, LABELS, check, clinic_request, cover_sheet, form, iso_date, read_fax, unchecked
from caduceus.payer import MockPayer, build_appeal_bundle, build_reply_bundle, build_request_bundle
from caduceus.smart import LaunchRefused, Read
from caduceus.storage import AuditStore, CaseStore, DocumentStore, FaxStore
from caduceus.tools import ToolGateway, build_gateway
from caduceus.words import clock, long_date, moment

# One worker, shared by every demo visitor's copy: a small model on a CPU answers one request at a time, in tens of
# seconds, so it never runs on a request's path.
MODEL_WORK = ThreadPoolExecutor(max_workers=1, thread_name_prefix="in-vpc-model")
UNDO_SECONDS = 30
NOTE_LIMIT = 4000  # characters
SNAPSHOT_FIELDS = ("state", "attestation_requested", "attestation_requested_at", "attestation_requested_by",
                   "attestations", "returned_reason", "submission", "payer_response", "draft", "reply", "appeal")
APPROVALS = ("case.approved", "reply.approved", "appeal.approved")
REMIND_EVERY = timedelta(minutes=10)
SIDE_PART = {"27447": "knee"}  # to ask "which knee?" rather than "which side?"
EHR_CASE_DUE = timedelta(hours=72)


class ActionRefused(Exception):
    """An action the case's state or the person's role does not allow. `detail` may name the form `field` it is
    about, or the `case_id` of a case that already covers it."""

    def __init__(self, message: str, **detail: str):
        super().__init__(message)
        self.detail = detail


def holds_the_case(method: Callable[..., Case]) -> Callable[..., Case]:
    """Hold the case from read to save, so the background summary cannot save in between and turn a person's action
    into a stale save after the audit log has recorded it."""
    @functools.wraps(method)
    def held(self: Workflow, who: Principal, case_id: str, *args: Any, **kwargs: Any) -> Case:
        with self._holding(case_id):
            return method(self, who, case_id, *args, **kwargs)
    return held


class Workflow:
    def __init__(self, cases: CaseStore, audit: AuditStore, store: FhirStore, library: dict[str, CriteriaSet],
                 engine: Engine, drafter: Drafter, payer: MockPayer,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC), faxes: FaxStore | None = None,
                 catalog: dict[str, Any] | None = None, fax_dir: Path | None = None, tz: tzinfo = UTC,
                 filed: DocumentStore | None = None, launched: EhrRecordRepository | None = None):
        self.cases, self.audit, self.store, self.library = cases, audit, store, library
        self.engine, self.drafter, self.payer, self.now = engine, drafter, payer, clock
        self.faxes, self.fax_dir, self.filed, self.launched, self.tz = faxes, fax_dir, filed, launched, tz
        self.later: Callable[..., Any] = MODEL_WORK.submit  # tests run background work at once instead
        self.catalog = catalog or {"services": {}, "clinicians": {}, "clinics": {}, "payers": set()}
        self._dispatch_lock = threading.Lock()  # one dispatcher at a time, so a request is never sent twice
        self._held: dict[str, threading.RLock] = {}
        self._held_guard = threading.Lock()
        self.gateway: ToolGateway = build_gateway(audit, store, library, self._set_state, self._submit,
                                                  self._payer_status, self._respond, self._appeal,
                                                  self.payer.coverage_requirements, self._read_fax)

    # The agent ---------------------------------------------------------------------------------------------------

    def prepare(self, case_id: str) -> Case:
        case = self._get(case_id)
        gw, cid = self.gateway, case.id
        common = {"patient_id": case.patient_id, "case_id": cid, "as_of": case.as_of}
        gw.call(AGENT, cid, "enrichment", "ehr.get_patient_summary", **common)
        gw.call(AGENT, cid, "enrichment", "ehr.list_active_conditions", **common)
        gw.call(AGENT, cid, "enrichment", "ehr.list_procedures", **common)
        gw.call(AGENT, cid, "enrichment", "documents.list_packet", packet=case.packet)
        gw.call(AGENT, cid, "enrichment", "policies.criteria_set_for", key=case.criteria_set)
        required = gw.call(AGENT, cid, "enrichment", "payer.coverage_requirements", payer_name=case.payer["name"],
                           service_code=case.service["code"], policy=case.criteria_set)

        record = self.store.record(case.patient_id, case.id)
        det = self.engine.evaluate(self.library[case.criteria_set], record, date.fromisoformat(case.as_of),
                                   side=case.side)
        case.determination, case.checked_at = det.to_dict(), self.now()
        # A payer publishing a different policy than the one approved here blocks approval until a person reviews it.
        case.requirements = required | {"matches": required["policy"]["sha256"] == det.content_sha256,
                                        "checked_at": self.now().isoformat()}
        self.audit.append(AGENT.id, AGENT.role, "criteria.evaluated", cid,
                          {"set": det.criteria_set, "set_sha256": det.content_sha256, "overall": det.overall,
                           "counts": det.counts, "result_sha256": sha256_of(det.to_dict())})
        case.leads = {r.criterion_id: gw.call(AGENT, cid, "enrichment", "documents.search_packet", packet=case.packet,
                                              criteria_set=case.criteria_set, criterion_id=r.criterion_id, k=3)
                      for r in det.results if r.status.value == "unknown"}

        self._draft(case, record.patient, reason="prepared")
        case = self.cases.save(case)
        gw.call(AGENT, cid, "review", "tickets.set_state", case_id=cid, state=State.REVIEW.value,
                reason="prepared for review")
        return self._get(cid)

    def _draft(self, case: Case, patient: dict, reason: str) -> None:
        """Write the draft from the attestation-aware results."""
        self.gateway.call(AGENT, case.id, "drafting", "documents.list_packet", packet=case.packet)
        draft = self.drafter.draft(case, patient)
        draft["generated_at"] = self.now().isoformat()
        case.draft = draft
        self.audit.append(AGENT.id, AGENT.role, "draft.generated", case.id,
                          {"reason": reason, "route": draft["route"], "model": draft.get("model"),
                           "deid_removed": draft.get("deid_removed"), "text_sha256": sha256_of(draft["text"])})

    def _refresh_draft_after_attestation(self, case: Case) -> None:
        """Rewrite the draft to match the signed answers. A draft a person edited is never overwritten: it is marked
        out of date, and approval waits until someone reads and saves it again."""
        if case.draft and case.draft.get("edited_by"):
            case.draft = case.draft | {"out_of_date": True}
            self.audit.append(AGENT.id, AGENT.role, "draft.out_of_date", case.id, {"reason": "attestation signed"})
            return
        self._draft(case, self.store.record(case.patient_id, case.id).patient, reason="attestation signed")

    def _draft_appeal(self, case_id: str) -> None:
        """Draft the appeal of a denial from the payer's reason, the policy and the record."""
        case = self._get(case_id)
        self.gateway.call(AGENT, case.id, "drafting", "documents.list_packet", packet=case.packet)
        appeal = self.drafter.appeal(case, self.store.record(case.patient_id, case.id).patient)
        case.appeal = appeal | {"generated_at": self.now().isoformat()}
        self.audit.append(AGENT.id, AGENT.role, "appeal.drafted", case.id,
                          {"route": appeal["route"], "model": appeal.get("model"),
                           "text_sha256": sha256_of(appeal["text"])})
        self.cases.save(case)

    def _write_summary(self, case_id: str, asked_at: str) -> None:
        """Write the in-network model's chart summary for the attestation request made at `asked_at`, in the
        background. The case is held only to save, never while the model writes, and the summary is kept only while
        the case still waits on that request. Whatever happens, the request stops saying a summary is on its way."""
        def waiting(c: Case) -> bool:
            return c.state == State.AWAITING_ATTESTATION and (c.chart_summary or {}).get("asked_at") == asked_at

        summary: dict[str, Any] | None = None
        try:
            case = self._get(case_id)
            if waiting(case):
                summary = self.drafter.chart_summary(case, self.store.record(case.patient_id, case.id).patient,
                                                     case.attestation_requested)
        except Exception as e:  # noqa: BLE001 - background work must not take the process down; it is audited
            self.audit.append(AGENT.id, AGENT.role, "summary.error", case_id, {"error": type(e).__name__})
        try:
            with self._holding(case_id):
                case = self._get(case_id)
                current = case.chart_summary or {}
                if summary is not None and waiting(case):
                    case.chart_summary = summary | {"generated_at": self.now().isoformat(), "asked_at": asked_at}
                    self.cases.save(case)
                    self.audit.append(AGENT.id, AGENT.role, "summary.written", case_id,
                                      {"route": summary["route"], "model": summary.get("model"),
                                       "text_sha256": sha256_of(summary["text"]),
                                       "replaced": summary.get("replaced", []),
                                       "fallback_reason": summary.get("fallback_reason")})
                elif current.get("asked_at") == asked_at and current.get("pending"):
                    case.chart_summary = current | {"pending": False}
                    self.cases.save(case)
        except Exception as e:  # noqa: BLE001 - the case store failed; the summary from the facts stays
            self.audit.append(AGENT.id, AGENT.role, "summary.error", case_id, {"error": type(e).__name__})

    def dispatch_due(self) -> list[str]:
        """Send what is out of its undo window, and read each decision the payer said would be ready. Safe to call
        often, from any thread."""
        moved = []
        with self._dispatch_lock:
            for case in self.cases.all():
                sub, waiting = case.submission or {}, case.payer_response or {}
                if case.state != State.SUBMITTED:
                    continue
                if not sub.get("sent") and datetime.fromisoformat(sub["send_after"]) <= self.now():
                    tool = {"reply": "payer.respond", "appeal": "payer.appeal"}.get(sub.get("kind", ""), "payer.submit")
                    result = self.gateway.call(AGENT, case.id, "submission", tool, case_id=case.id,
                                               approval_seq=sub["approval_seq"])
                    self._follow_up(case.id, result["id"])
                    moved.append(case.id)
                elif sub.get("sent") and waiting.get("in_review") and \
                        datetime.fromisoformat(waiting["expected_by"]) <= self.now():
                    # Asked once, when the payer said it would decide, so waiting adds nothing to the audit log.
                    if self._follow_up(case.id, waiting["id"]):
                        moved.append(case.id)
        return moved

    def _follow_up(self, case_id: str, submission_id: str) -> bool:
        """Read the payer's decision and act on it. False while the payer is still reviewing."""
        decision = self.gateway.call(AGENT, case_id, "follow_up", "payer.status", submission_id=submission_id)
        case = self._get(case_id)
        if decision.get("in_review"):
            case.payer_response = {k: decision[k] for k in ("id", "submitted_at", "in_review", "expected_by",
                                                            "priority")}
            self.cases.save(case)
            return False
        case.payer_response = decision
        new_state = {"A1": State.APPROVED, "A3": State.DENIED}.get(decision["decision"]["code"], State.PENDED)
        if new_state == State.PENDED:
            case.reply = None  # a new question starts a new reply
        elif case.decided_at is None:
            case.decided_at = self.now()  # the decision clock stops at the first approval or denial
        case = self.cases.save(case)
        self.gateway.call(AGENT, case.id, "follow_up", "tickets.set_state", case_id=case.id,
                          state=new_state.value, reason=decision["decision"]["display"])
        if new_state == State.DENIED:
            self._draft_appeal(case.id)
        return True

    # The request: review, attestation and approval ---------------------------------------------------------------

    @holds_the_case
    def set_urgency(self, who: Principal, case_id: str, expedited: bool, reason: str = "") -> Case:
        """Ask the payer for an expedited decision, which needs its clinical reason, or go back to standard."""
        self._require(who, Role.PA_SPECIALIST)
        case = self._get(case_id)
        if case.state not in (State.REVIEW, State.AWAITING_ATTESTATION, State.RETURNED):
            raise ActionRefused("The request has been approved, so its urgency can no longer change.")
        reason = " ".join(reason.split())
        if expedited and not reason:
            raise ActionRefused("Enter the clinical reason this cannot wait the standard time.", field="reason")
        case.urgency, case.urgency_reason = ("expedited", reason) if expedited else ("standard", None)
        self.audit.append(who.id, who.role, "case.urgency_changed", case_id,
                          {"urgency": case.urgency, "reason_sha256": sha256_of(reason) if expedited else None})
        return self.cases.save(case)

    @holds_the_case
    def request_attestation(self, who: Principal, case_id: str) -> Case:
        self._require(who, Role.PA_SPECIALIST)
        case = self._get(case_id)
        unknown = [r["criterion_id"] for r in case.effective_results() if r["status"] == "unknown"]
        if case.state != State.REVIEW:
            raise ActionRefused("This case is not waiting for your review.")
        if not unknown:
            raise ActionRefused("Every criterion already has an answer, so no attestation is needed.")
        self._remember(case, who, "attestation.requested")
        case.state, case.attestation_requested, case.attestation_requested_at = \
            State.AWAITING_ATTESTATION, unknown, self.now()
        case.attestation_requested_by = who.name
        # The clinician never waits for a model: the summary from the record's facts now, the in-network model's later.
        asked_at = case.attestation_requested_at.isoformat()
        case.chart_summary = self.drafter.template_summary(case, unknown) | {
            "generated_at": self.now().isoformat(), "pending": self.drafter.phi_route, "asked_at": asked_at}
        self.audit.append(who.id, who.role, "attestation.requested", case_id,
                          {"criteria": unknown, "clinician": case.ordering_clinician["id"]})
        saved = self.cases.save(case)
        if self.drafter.phi_route:
            self.later(self._write_summary, case_id, asked_at)
        return saved

    @holds_the_case
    def sign_attestation(self, who: Principal, case_id: str, answers: dict[str, str]) -> Case:
        self._require(who, Role.CLINICIAN)
        case = self._get(case_id)
        if who.clinician_id != case.ordering_clinician["id"]:
            raise ActionRefused("Only the ordering clinician can attest for this request.")
        if case.state != State.AWAITING_ATTESTATION:
            raise ActionRefused("This request is not waiting for your signature.")
        if set(answers) != set(case.attestation_requested) or not set(answers.values()) <= {"yes", "no"}:
            raise ActionRefused("Answer each question with yes or no.")
        self._remember(case, who, "attestation.signed")
        signed_at = self.now()
        case.attestations += [Attestation(criterion_id=c, answer=a, clinician_id=who.clinician_id,
                                          clinician_name=who.name, signed_at=signed_at) for c, a in answers.items()]
        case.state, case.attestation_requested = State.REVIEW, []
        self.audit.append(who.id, who.role, "attestation.signed", case_id, {"answers": answers})
        self._refresh_draft_after_attestation(case)
        return self.cases.save(case)

    @holds_the_case
    def edit_draft(self, who: Principal, case_id: str, text: str) -> Case:
        self._require(who, Role.PA_SPECIALIST)
        case = self._get(case_id)
        if case.state != State.REVIEW:
            raise ActionRefused("The draft can only be edited while the case is under review.")
        if not text.strip():
            raise ActionRefused("The draft cannot be empty.")
        case.draft = (case.draft or {}) | {"text": text.strip(), "edited_by": who.name,
                                           "edited_at": self.now().isoformat(), "out_of_date": False}
        self.audit.append(who.id, who.role, "draft.edited", case_id, {"text_sha256": sha256_of(text.strip())})
        return self.cases.save(case)

    @holds_the_case
    def approve(self, who: Principal, case_id: str) -> Case:
        self._require(who, Role.PA_SPECIALIST)
        case = self._get(case_id)
        if not case.ready_to_approve:
            raise ActionRefused(approve_blocked_reason(case) or "This case cannot be approved yet.")
        return self._to_outbox(who, case, "case.approved",
                               {"draft_sha256": sha256_of(case.draft["text"] if case.draft else "")})

    @holds_the_case
    def return_for_correction(self, who: Principal, case_id: str, reason: str) -> Case:
        self._require(who, Role.PA_SPECIALIST)
        case = self._get(case_id)
        if case.state not in (State.REVIEW, State.PENDED, State.DENIED):
            raise ActionRefused("Only a case under review can be returned.")
        if not reason.strip():
            raise ActionRefused("Say what needs correcting so the clinic can fix it.")
        self._remember(case, who, "case.returned")
        case.state, case.returned_reason = State.RETURNED, reason.strip()
        self.audit.append(who.id, who.role, "case.returned", case_id, {"reason_sha256": sha256_of(reason)})
        return self.cases.save(case)

    @holds_the_case
    def resubmit(self, who: Principal, case_id: str) -> Case:
        """Prepare a returned case again from the start once the clinic resubmits it. Signatures given for the
        referral as it was do not carry over; the earlier exchange with the payer stays in the case history."""
        self._require(who, Role.PA_SPECIALIST)
        case = self._get(case_id)
        if case.state != State.RETURNED:
            raise ActionRefused("Only a case returned to the clinic can be marked resubmitted.")
        self.audit.append(who.id, who.role, "case.resubmitted", case_id,
                          {"returned_reason_sha256": sha256_of(case.returned_reason or "")})
        fresh = case.model_copy(update={
            "state": State.NEW, "returned_reason": None, "draft": None, "attestations": [],
            "attestation_requested": [], "attestation_requested_at": None, "attestation_requested_by": None,
            "submission": None, "payer_response": None, "reply": None, "appeal": None, "sent_at": None,
            "decided_at": None, "last_action": None})
        self.cases.save(fresh)
        return self.prepare(case_id)

    @holds_the_case
    def remind(self, who: Principal, case_id: str) -> Case:
        """Remind the ordering clinician of the signature or note the case waits on: at most once every
        REMIND_EVERY per case, whoever sends it. The reminder shows on their list; nothing else is sent."""
        self._require(who, Role.PA_SPECIALIST)
        case = self._get(case_id)
        clin = case.ordering_clinician["name"]
        if case.waiting_since() is None:
            raise ActionRefused(f"This case is not waiting on {clin}.")
        sent = case.reminders_since_asked()
        if sent:
            last = datetime.fromisoformat(sent[-1]["at"])
            if self.now() - last < REMIND_EVERY:
                raise ActionRefused(f"{clin} was reminded at {clock(last, self.tz)}. You can send another reminder at "
                                    f"{clock(last + REMIND_EVERY, self.tz)}.")
        case.reminders = [*case.reminders, {"by": who.name, "by_id": who.id, "at": self.now().isoformat()}]
        self.audit.append(who.id, who.role, "clinician.reminded", case_id,
                          {"clinician": case.ordering_clinician["id"], "count": len(sent) + 1,
                           "kind": "note" if case.state == State.AWAITING_NOTE else "attestation"})
        return self.cases.save(case)

    # The payer's question ----------------------------------------------------------------------------------------

    @holds_the_case
    def request_note(self, who: Principal, case_id: str) -> Case:
        """Ask the ordering clinician for the documentation the payer asked for when it pended the request."""
        self._require(who, Role.PA_SPECIALIST)
        case = self._get(case_id)
        if case.state != State.PENDED:
            raise ActionRefused("The payer is not waiting for anything on this case.")
        if (case.reply or {}).get("note"):
            raise ActionRefused(f"{case.ordering_clinician['name']} already signed the note. Send it to the payer.")
        self._remember(case, who, "note.requested")
        case.state = State.AWAITING_NOTE
        case.reply = {"question": (case.payer_response or {}).get("decision", {}).get("question"),
                      "criteria": [a.criterion_id for a in case.attestations if a.answer == "yes"],
                      "requested_by": who.name, "requested_at": self.now().isoformat()}
        self.audit.append(who.id, who.role, "note.requested", case_id, {"clinician": case.ordering_clinician["id"]})
        return self.cases.save(case)

    @holds_the_case
    def sign_note(self, who: Principal, case_id: str, text: str) -> Case:
        """The clinician's own words, signed and dated now: an addendum to the chart, never backdated."""
        case = self._clinician_note_case(who, case_id)
        text = text.strip()
        if not text:
            raise ActionRefused("Write the note before you sign it.")
        if len(text) > NOTE_LIMIT:
            raise ActionRefused(f"Keep the note under {NOTE_LIMIT:,} characters.")
        self._remember(case, who, "note.signed")
        case.reply = (case.reply or {}) | {"note": {"text": text, "author": who.name, "clinician_id": who.clinician_id,
                                                    "signed_at": self.now().isoformat()}}
        case.state = State.PENDED
        self.audit.append(who.id, who.role, "note.signed", case_id, {"text_sha256": sha256_of(text)})
        return self.cases.save(case)

    @holds_the_case
    def decline_note(self, who: Principal, case_id: str) -> Case:
        """The clinician cannot document what the payer asked for. Nothing is sent; the specialist decides next."""
        case = self._clinician_note_case(who, case_id)
        self._remember(case, who, "note.declined")
        case.reply = (case.reply or {}) | {"declined_by": who.name, "declined_at": self.now().isoformat()}
        case.state = State.PENDED
        self.audit.append(who.id, who.role, "note.declined", case_id)
        return self.cases.save(case)

    @holds_the_case
    def approve_reply(self, who: Principal, case_id: str) -> Case:
        """Approve sending the clinician's signed note to the payer."""
        self._require(who, Role.PA_SPECIALIST)
        case = self._get(case_id)
        reply = case.reply or {}
        if case.state != State.PENDED:
            raise ActionRefused("The payer is not waiting for anything on this case.")
        if not reply.get("note"):
            raise ActionRefused(f"{case.ordering_clinician['name']} could not document this, so there is no note "
                                "to send." if reply.get("declined_by")
                                else f"Ask {case.ordering_clinician['name']} for the note first.")
        return self._to_outbox(who, case, "reply.approved", {"note_sha256": sha256_of(reply["note"]["text"])},
                               kind="reply")

    # Appeals -----------------------------------------------------------------------------------------------------

    @holds_the_case
    def edit_appeal(self, who: Principal, case_id: str, text: str) -> Case:
        self._require(who, Role.PA_SPECIALIST)
        case = self._get(case_id)
        if case.state != State.DENIED or not case.appeal:
            raise ActionRefused("There is no appeal to edit on this case.")
        if not text.strip():
            raise ActionRefused("The appeal cannot be empty.")
        case.appeal = case.appeal | {"text": text.strip(), "edited_by": who.name, "edited_at": self.now().isoformat()}
        self.audit.append(who.id, who.role, "appeal.edited", case_id, {"text_sha256": sha256_of(text.strip())})
        return self.cases.save(case)

    @holds_the_case
    def approve_appeal(self, who: Principal, case_id: str) -> Case:
        self._require(who, Role.PA_SPECIALIST)
        case = self._get(case_id)
        if case.state != State.DENIED or not case.appeal:
            raise ActionRefused("There is no denial to appeal on this case.")
        return self._to_outbox(who, case, "appeal.approved", {"appeal_sha256": sha256_of(case.appeal["text"])},
                               kind="appeal")

    # Undo and recall ---------------------------------------------------------------------------------------------

    @holds_the_case
    def undo(self, who: Principal, case_id: str) -> Case:
        case = self._get(case_id)
        last = case.last_action
        if last is None or last.actor != who.id:
            raise ActionRefused("There is nothing of yours to undo on this case.")
        if case.sent_since_last_action:
            raise ActionRefused(self._reached(case))
        if self.now() > last.undo_until:
            if case.state == State.SUBMITTED:
                raise ActionRefused("The 30 seconds to undo have passed, but it has not been sent yet. Recall it "
                                    "instead.")
            raise ActionRefused("The 30 seconds to undo have passed.")
        restored = self._restored(case, last)
        self.audit.append(who.id, who.role, "action.undone", case_id, {"undone": last.action})
        return self.cases.save(restored)

    @holds_the_case
    def recall(self, who: Principal, case_id: str) -> Case:
        """Take back an approval the outbox has not sent yet, after its undo window, returning the case to where the
        approval found it. Any PA specialist may, not only the one who approved."""
        self._require(who, Role.PA_SPECIALIST)
        with self._dispatch_lock:  # never while the outbox is sending it
            case = self._get(case_id)
            sub = case.submission or {}
            if sub.get("sent"):
                raise ActionRefused(self._reached(case))
            if case.state != State.SUBMITTED:
                raise ActionRefused("Nothing approved is waiting to be sent on this case.")
            last = case.last_action
            if last is None or last.action not in APPROVALS:
                raise ActionRefused("This approval cannot be recalled here. Ask an administrator.")
            self.audit.append(who.id, who.role, "case.recalled", case_id,
                              {"kind": sub.get("kind") or "request", "approval_seq": sub.get("approval_seq"),
                               "approved_by": sub.get("approved_by_id")})
            return self.cases.save(self._restored(case, last))

    def _reached(self, case: Case) -> str:
        """The refusal for undoing or recalling what already reached the payer, saying when it did."""
        sub = case.submission or {}
        what = {"reply": "The note", "appeal": "The appeal"}.get(sub.get("kind", ""), "The request")
        when = f" {moment(sub['sent_at'], self.now(), self.tz)}" if sub.get("sent_at") else ""
        return f"{what} reached {case.payer['name']}{when}, so it cannot be undone or recalled."

    @staticmethod
    def _restored(case: Case, last: LastAction) -> Case:
        return Case.model_validate(case.model_dump(mode="json") | last.prior | {"last_action": None})

    # Faxed referrals ---------------------------------------------------------------------------------------------

    def known(self) -> dict[str, set[str]]:
        return {"payers": set(self.catalog["payers"]), "clinicians": set(self.catalog["clinicians"]),
                "codes": set(self.catalog["services"])}

    def read_faxes(self) -> list[str]:
        """Read each fax that has arrived with OCR, inside the boundary, and mark what a person must check. Safe to
        call often: a fax is read once."""
        if self.faxes is None:
            return []
        read = []
        for fax in self.faxes.all():
            if fax.status != "received":
                continue
            reading = self.gateway.call(AGENT, fax.id, "intake", "documents.read_fax", fax_file=fax.file)
            fields = check(reading["fields"], self.known())
            fax.reading, fax.status = reading | {"fields": fields}, "read"
            self.faxes.save(fax)
            self.audit.append(AGENT.id, AGENT.role, "fax.read", fax.id,
                              {"sha256": reading["sha256"], "fields": len(fields),
                               "to_check": sum(1 for f in fields if f["problem"])})
            read.append(fax.id)
        return read

    def intake_form(self, fax: Fax) -> dict[str, Any]:
        return form(fax.reading or {}, self.catalog)

    def match_patient(self, who: Principal, fax_id: str, name: str, birth_date: str,
                      ref: str | None = None) -> dict[str, Any]:
        """Look the faxed patient up in the chart: the one record with exactly this name and date of birth, otherwise
        the closest few for the person to choose from (`ref` names their choice). A record is named by a reference
        made for this fax, never by its record number."""
        self._require(who, Role.PA_SPECIALIST)
        fax = self._fax(fax_id)
        if fax.status != "read":
            raise ActionRefused("This fax is not waiting to be checked.")
        born = iso_date(birth_date.strip()) or ""
        exact = self.store.find_patient(name, born) if name.strip() and born else None
        nearest = self.store.candidates(name, born) if name.strip() else []

        def ref_of(patient_id: str) -> str:
            return sha256_of(f"{fax.id}:{patient_id}")[:16]

        chosen = next((p for p in nearest if ref_of(p["id"]) == ref), None) if ref and not exact else None
        if ref and not exact and chosen is None:
            raise ActionRefused("That record is no longer among the closest to this name and date of birth. "
                                "Look the patient up again.")
        patient = self.store.record(exact).patient if exact else chosen
        status = "matched" if exact else "chosen" if chosen else "candidates" if nearest else "none"
        self.audit.append(who.id, who.role, "fax.patient_searched", fax.id,
                          {"result": status, "candidates": 0 if patient else len(nearest)})

        def card(p: dict) -> dict[str, Any]:
            d = date.fromisoformat(p["birthDate"])
            return {"ref": ref_of(p["id"]), "name": patient_name(p), "born": long_date(d),
                    "birth_date": f"{d:%m/%d/%Y}", "city": ((p.get("address") or [{}])[0]).get("city")}

        return {"status": status, "patient": card(patient) if patient else None,
                "on_file": self.catalog.get("coverage", {}).get(patient["id"]) if patient else None,
                "candidates": [] if patient else [card(p) for p in nearest]}

    def ask_clinic(self, who: Principal, fax_id: str, missing: list[str], resend: bool = False,
                   referral: str | None = None) -> Fax:
        """Write the request to the sending clinic for the fields the fax does not clearly give (or, with `resend`,
        for the whole referral again), and hold the fax until they answer. A person sends it; Caduceus does not."""
        self._require(who, Role.PA_SPECIALIST)
        fax = self._fax(fax_id)
        if fax.status == "waiting_on_clinic":
            raise ActionRefused("The clinic has already been asked. Mark that they sent it first.")
        if fax.status != "read":
            raise ActionRefused("This fax is not waiting to be checked.")
        if set(missing) - set(FORM):
            raise ActionRefused("Choose the fields the fax does not give from the form.")
        keys = [k for k in FORM if k in set(missing)]
        if not keys and not resend:
            raise ActionRefused("Choose what the fax does not give clearly, then ask the clinic.")
        checking = self.intake_form(fax)
        read = {f["key"]: f for f in checking["fields"]}["referral"]
        # The referral is named only by a number a person gave or the fax gave clearly, never by a guess.
        number = referral if referral and re.fullmatch(r"\d{4}-\d{4}", referral) else \
            read["read_as"] if not read["problem"] else None
        received = self.now().astimezone(self.tz)
        at = fax.received_at.astimezone(self.tz)
        text = clinic_request(checking["clinic"], None if "referral" in keys else number,
                              f"{long_date(at.date())} at {clock(at, self.tz)}", keys, resend)
        fax.status = "waiting_on_clinic"
        fax.request = {"kind": "resend" if resend else "missing", "missing": keys, "to": checking["clinic"],
                       "text": text, "asked_by": who.name, "asked_at": received.isoformat()}
        self._inbox().save(fax)
        self.audit.append(who.id, who.role, "fax.clinic_asked", fax.id,
                          {"kind": fax.request["kind"], "missing": keys, "to": checking["clinic"],
                           "text_sha256": sha256_of(text)})
        return fax

    def clinic_sent(self, who: Principal, fax_id: str) -> Fax:
        """The clinic answered: the fax can be checked and finished."""
        self._require(who, Role.PA_SPECIALIST)
        fax = self._fax(fax_id)
        if fax.status != "waiting_on_clinic":
            raise ActionRefused("This fax is not waiting on the clinic.")
        fax.status = "read"
        fax.request = (fax.request or {}) | {"answered_by": who.name, "answered_at": self.now().isoformat()}
        self._inbox().save(fax)
        self.audit.append(who.id, who.role, "fax.clinic_replied", fax.id, {"kind": fax.request.get("kind")})
        return fax

    def confirm_fax(self, who: Principal, fax_id: str, values: dict[str, str],
                    checked: list[str] | None = None) -> Case:
        """Create the case once a person has confirmed the fields against the page. Every field sent to a person must
        be ticked as matching the fax or corrected, so nothing they did not look at gets through."""
        self._require(who, Role.PA_SPECIALIST)
        fax = self._fax(fax_id)
        if fax.status != "read":
            why = {"confirmed": "This fax has already been turned into a case.",
                   "waiting_on_clinic": "This fax is waiting on the clinic. Mark that they sent it, then finish "
                                        "checking it."}
            raise ActionRefused(why.get(fax.status, "Caduceus has not read this fax yet."))
        side = str(values.get("side") or "").strip().lower()
        values = {key: str(values.get(key) or "").strip() for key, _ in LABELS}
        service = self.catalog["services"].get(values["service_code"])
        values["service"] = service["display"] if service else ""  # named from the catalog, not by the request
        self._refuse_invalid(values)
        fields = [f for f in self.intake_form(fax)["fields"]
                  if f["key"] != "side" or (service or {}).get("needs_side")]
        missed = unchecked(fields, values | {"side": side}, checked or [])
        if missed:
            raise ActionRefused(f"Check the {mid_sentence(missed[0]['label'])} against the fax, then tick "
                                "“Matches the fax”, or correct it.", field=missed[0]["key"])
        patient_id = self.store.find_patient(values["patient_name"], iso_date(values["birth_date"]) or "")
        if patient_id is None:
            raise ActionRefused(f"No patient named {values['patient_name']} born {values['birth_date']} is in the "
                                "record. Check the name and date of birth against the fax.", field="patient_name")
        if self.cases.get(values["referral"]) is not None:
            raise ActionRefused(f"A case for referral {values['referral']} already exists.", field="referral",
                                case_id=values["referral"])
        if service.get("needs_side") and side not in ("right", "left"):
            raise ActionRefused("Say which side the request is for: the criteria check that the diagnosis is on "
                                "that side.", field="side")
        requested_side = _side(side) if service.get("needs_side") else None
        theirs = (c for c in sorted(self.cases.all(), key=lambda c: c.id) if c.patient_id == patient_id)
        same = _open_duplicate(theirs, values["service_code"], requested_side)
        if same is not None:
            raise ActionRefused(f"Case {same.id} is already open for this patient and this service, so this referral "
                                "would be a second request. Open that case instead.", field="service_code",
                                case_id=same.id)
        read = {f["key"]: f for f in (fax.reading or {}).get("fields", [])}
        flagged = [f["key"] for f in fields if f["problem"]]
        packet = self._file_cover_sheet(fax)
        case = Case(id=values["referral"], patient_id=patient_id,
                    service={k: service[k] for k in ("system", "code", "display", "short")},
                    criteria_set=service["criteria_set"], side=requested_side, as_of=fax.as_of,
                    payer={"name": values["payer"], "member_id_last4": values["member_id"][-4:]},
                    ordering_clinician=self.catalog["clinicians"][values["clinician"]], packet=packet,
                    received_at=fax.received_at, due_at=fax.received_at + timedelta(hours=fax.due_in_hours))
        self.cases.save(case)
        self.audit.append(who.id, who.role, "fax.confirmed", fax.id, {
            "case": case.id, "checked": flagged, "ticked": sorted(set(checked or []) & set(flagged)),
            "corrected": [k for k in values if values[k] != read.get(k, {}).get("value")]})
        self.audit.append("intake", "agent_runtime", "case.received", case.id,
                          {"service": case.service["code"], "pages": len(case.packet), "from": fax.id})
        fax.status, fax.case_id = "confirmed", case.id
        self._inbox().save(fax)
        return self.prepare(case.id)

    def _refuse_invalid(self, values: dict[str, str]) -> None:
        """Refuse the first field a person entered that is not valid: malformed, or not a plan, clinician or service
        on file."""
        entered = [{"key": k, "label": label, "value": values[k], "confidence": 1.0, "box": None}
                   for k, label in LABELS]
        problems = [f for f in check(entered, self.known()) if f["problem"]]
        if problems:
            p = problems[0]
            if p["key"] in ("service_code", "service"):
                raise ActionRefused(f"Check the requested service: {p['problem']}", field="service_code")
            raise ActionRefused(f"Check the {mid_sentence(p['label'])}: {p['problem']}", field=p["key"])

    def _file_cover_sheet(self, fax: Fax) -> list[str]:
        """The new case's packet: the fax's pages, after its cover sheet as page 1 when documents can be filed."""
        if self.filed is None:
            return list(fax.pages)
        cover = cover_sheet(f"cover-{fax.id.lower()}", fax.file, fax.reading or {}, fax.received_at.isoformat(),
                            self.intake_form(fax)["clinic"])
        self.filed.save(cover)
        return [cover["id"], *fax.pages]

    # Records from an EHR -----------------------------------------------------------------------------------------

    def in_use(self) -> dict[str, CriteriaSet]:
        """The criteria sets the team's services run under: what a record from an EHR is read for."""
        keys = {s["criteria_set"] for s in self.catalog["services"].values()}
        return {k: v for k, v in self.library.items() if k in keys}

    def file_ehr_record(self, who: Principal, read: Read, keep: int | None = None) -> str:
        """File the record a SMART launch read and return its key. A new read of the same patient from the same EHR
        replaces their record, unless a case was made from it, which keeps the record it was checked against. `keep`
        caps the patients one copy holds, letting the oldest that no case uses go first. A record the engine, the
        drafter or the screens cannot read is not filed."""
        self._require(who, Role.PA_SPECIALIST)
        if self.launched is None:
            raise ActionRefused("This system does not keep records from an EHR.")
        as_of = date.fromisoformat(read.as_of)
        try:
            record = PatientRecord(read.patient, list(read.resources))
            for criteria in self.in_use().values():
                self.engine.evaluate(criteria, record, as_of)
            patient_name(read.patient)
            age_on(read.patient, as_of)
            patient_context(read.patient)
        except (AttributeError, IndexError, KeyError, TypeError, ValueError) as e:
            raise LaunchRefused("bad_response", step="record", error=type(e).__name__) from e
        used = {c.patient_id for c in self.cases.all()}
        key = self.launched.find(read.match)
        if key is not None and key in used:
            self.launched.retire(key)
            key = None
        if key is None:
            kept = self.launched.keys_oldest_first()
            if keep is not None and len(kept) >= keep:
                spare = [k for k in kept if k not in used]
                if not spare:
                    raise ActionRefused(f"This copy of the demo already holds {keep} patients opened from an EHR, "
                                        "each with a case. Start over to open another.")
                self.launched.delete(spare[0])
            key = new_ehr_key()
        self.launched.save(key, read.match, {
            "key": key, "match": read.match, "iss": read.iss, "read_at": self.now().isoformat(), "read_by": who.id,
            "as_of": read.as_of,
            "patient": read.patient, "resources": read.resources, "notes": read.notes, "counts": read.counts,
            "not_shared": read.not_shared, "windows": read.windows})
        self.audit.append(who.id, who.role, "ehr.record_read", key, read.audit)
        return key

    def ehr_preview(self, key: str) -> list[dict[str, Any]]:
        """For each service this team handles, how many of its criteria the record answers today, before any case
        exists."""
        record = self.store.record(key)
        as_of = self.now().astimezone(self.tz).date()
        out = []
        for code, service in sorted(self.catalog["services"].items(), key=lambda kv: kv[1]["short"]):
            counts = self.engine.evaluate(self.library[service["criteria_set"]], record, as_of).counts
            clinician = self.catalog.get("ordering", {}).get(code)
            out.append({"code": code, "label": service["short"], "display": service["display"],
                        "total": sum(counts.values()), "met": counts["met"], "unmet": counts["unmet"],
                        "unknown": counts["unknown"], "clinician": clinician["name"] if clinician else None,
                        "side": SIDE_PART.get(code, "side") if service.get("needs_side") else None})
        return out

    def patient_cases(self, key: str) -> list[Case]:
        """The cases made from this record, or from an earlier read of the same patient from the same EHR."""
        match = ((self.launched.get(key) if self.launched else None) or {}).get("match")

        def theirs(patient_id: str) -> bool:
            if patient_id == key:
                return True
            earlier = self.launched.get(patient_id) if self.launched and match else None
            return earlier is not None and earlier.get("match") == match

        return [c for c in sorted(self.cases.all(), key=lambda c: c.id) if theirs(c.patient_id)]

    def open_from_ehr(self, who: Principal, key: str, service_code: str, side: str | None, payer: str) -> Case:
        """Start a request from a record an EHR shared. The person names the service, its side and the health plan,
        since the launch reads no coverage; the team's ordering clinician for the service confirms what the record
        does not answer."""
        self._require(who, Role.PA_SPECIALIST)
        data = self.launched.get(key) if self.launched else None
        if data is None:
            raise ActionRefused("That record from the EHR is no longer here. Open Caduceus from the EHR again.")
        service = self.catalog["services"].get(service_code)
        if service is None:
            raise ActionRefused("Choose what is being requested.", field="service")
        sided = bool(service.get("needs_side"))
        if sided and side not in ("left", "right"):
            raise ActionRefused(f"Choose which {SIDE_PART.get(service_code, 'side')} the request is for.",
                                field="side")
        if payer not in self.catalog["payers"]:
            raise ActionRefused("Choose the health plan.", field="payer")
        requested_side = _side(side) if sided else None
        same = _open_duplicate(self.patient_cases(key), service_code, requested_side)
        if same is not None:
            raise ActionRefused(f"Case {same.id} is already open for this patient and this service, so this would "
                                "be a second request. Open that case instead.", field="service", case_id=same.id)
        named = {k: service[k] for k in ("system", "code", "display", "short")}
        now = self.now()
        numbers = [int(c.id[4:]) for c in self.cases.all() if re.fullmatch(r"EHR-\d{4}", c.id)]
        case = Case(id=f"EHR-{max(numbers, default=0) + 1:04d}", patient_id=key, service=named,
                    criteria_set=service["criteria_set"], side=requested_side,
                    as_of=now.astimezone(self.tz).date().isoformat(), payer={"name": payer, "member_id_last4": ""},
                    ordering_clinician=self.catalog["ordering"][service_code],
                    packet=[f"{key}-{n}" for n in range(1, len(data["notes"]) + 1)], received_at=now,
                    due_at=now + EHR_CASE_DUE)
        self.cases.save(case)
        self.audit.append(who.id, who.role, "case.received", case.id,
                          {"service": service_code, "pages": len(case.packet), "source": "smart_launch",
                           "iss": data["iss"], "record": key} | ({"side": requested_side} if requested_side else {}))
        return self.prepare(case.id)

    # The tools behind the gateway --------------------------------------------------------------------------------

    def _set_state(self, case_id: str, state: str, reason: str) -> None:
        case = self._get(case_id)
        case.state = State(state)
        self.cases.save(case)

    def _read_fax(self, fax_file: str) -> dict[str, Any]:
        """The documents.read_fax tool. Reads only the intake folder's own fax images."""
        if self.fax_dir is None or not re.fullmatch(r"FAX-\d{4}\.png", fax_file):
            raise ActionRefused("There is no such fax.")
        return read_fax(self.fax_dir / fax_file)

    def _payer_status(self, submission_id: str) -> dict[str, Any]:
        return self.payer.status(submission_id, now=self.now())

    def _outbox(self, case_id: str, approval_seq: int, action: str, kind: str | None) -> tuple[Case, Any]:
        """What a payer tool may send: only what a person approved, as the audit log records, and only while that
        approval is the one waiting in the outbox, so an approval that was undone cannot be replayed."""
        approvals = [e for e in self.audit.events(case_id)
                     if e.seq == approval_seq and e.action == action and e.role == Role.PA_SPECIALIST]
        if not approvals:
            raise ActionRefused("No human approval is recorded for this submission.")
        case = self._get(case_id)
        sub = case.submission or {}
        if case.state != State.SUBMITTED or sub.get("sent") or sub.get("approval_seq") != approval_seq \
                or sub.get("kind") != kind:
            raise ActionRefused("Nothing approved is waiting to be sent for this case.")
        return case, approvals[0]

    def _submit(self, case_id: str, approval_seq: int) -> dict[str, Any]:
        """The payer.submit tool. Sends the request a person approved, and nothing else."""
        case, _ = self._outbox(case_id, approval_seq, "case.approved", None)
        case.sent_at = case.sent_at or self.now()
        result = self.payer.submit(build_request_bundle(case), case.payer["name"], now=self.now())
        return self._sent(case, result)

    def _respond(self, case_id: str, approval_seq: int) -> dict[str, Any]:
        """The payer.respond tool. Sends the note a person approved, exactly as it was when they approved it."""
        case, approval = self._outbox(case_id, approval_seq, "reply.approved", "reply")
        note = (case.reply or {}).get("note") or {}
        if sha256_of(note.get("text", "")) != approval.detail.get("note_sha256"):
            raise ActionRefused("The note changed after it was approved.")
        result = self.payer.respond(build_reply_bundle(case), case.payer["name"], (case.payer_response or {})["id"],
                                    now=self.now())
        return self._sent(case, result)

    def _appeal(self, case_id: str, approval_seq: int) -> dict[str, Any]:
        """The payer.appeal tool. Sends the appeal a person approved, exactly as it was when they approved it."""
        case, approval = self._outbox(case_id, approval_seq, "appeal.approved", "appeal")
        if sha256_of((case.appeal or {}).get("text", "")) != approval.detail.get("appeal_sha256"):
            raise ActionRefused("The appeal changed after it was approved.")
        result = self.payer.appeal(build_appeal_bundle(case), case.payer["name"], (case.payer_response or {})["id"],
                                   now=self.now())
        return self._sent(case, result)

    def _sent(self, case: Case, result: dict[str, Any]) -> dict[str, Any]:
        case.submission = (case.submission or {}) | {"sent": True, "sent_at": self.now().isoformat(),
                                                     "submission_id": result["id"]}
        self.cases.save(case)
        return result

    # Internals ---------------------------------------------------------------------------------------------------

    @contextmanager
    def _holding(self, case_id: str) -> Iterator[None]:
        with self._held_guard:
            lock = self._held.setdefault(case_id, threading.RLock())
        with lock:
            yield

    def _get(self, case_id: str) -> Case:
        case = self.cases.get(case_id)
        if case is None:
            raise KeyError(case_id)
        return case

    def _fax(self, fax_id: str) -> Fax:
        fax = self.faxes.get(fax_id) if self.faxes else None
        if fax is None:
            raise KeyError(fax_id)
        return fax

    def _inbox(self) -> FaxStore:
        if self.faxes is None:
            raise KeyError("faxes")
        return self.faxes

    @staticmethod
    def _require(who: Principal, role: Role) -> None:
        if who.role != role:
            raise ActionRefused("Your role cannot take this action.")

    def _clinician_note_case(self, who: Principal, case_id: str) -> Case:
        self._require(who, Role.CLINICIAN)
        case = self._get(case_id)
        if who.clinician_id != case.ordering_clinician["id"]:
            raise ActionRefused("Only the ordering clinician can write this note.")
        if case.state != State.AWAITING_NOTE:
            raise ActionRefused("This request is not waiting for a note from you.")
        return case

    def _remember(self, case: Case, who: Principal, action: str) -> None:
        """Keep what `action` is about to change, so the person can undo it within the undo window."""
        prior = {f: case.model_dump(mode="json")[f] for f in SNAPSHOT_FIELDS}
        case.last_action = LastAction(action=action, actor=who.id, at=self.now(),
                                      undo_until=self.now() + timedelta(seconds=UNDO_SECONDS), prior=prior)

    def _to_outbox(self, who: Principal, case: Case, action: str, detail: dict[str, Any],
                   kind: str | None = None) -> Case:
        """Record a person's approval, then hold what it approved in the outbox until the undo window has passed."""
        event = self.audit.append(who.id, who.role, action, case.id, detail)
        self._remember(case, who, action)
        now = self.now()
        case.state = State.SUBMITTED
        case.submission = ({"kind": kind} if kind else {}) | {
            "approval_seq": event.seq, "approved_by": who.name, "approved_by_id": who.id,
            "approved_at": now.isoformat(), "sent": False,
            "send_after": (now + timedelta(seconds=UNDO_SECONDS)).isoformat()}
        return self.cases.save(case)


def approve_blocked_reason(case: Case) -> str | None:
    """Why the case cannot be approved yet, in words; None when nothing stands in the way."""
    c = case.effective_counts()
    if case.state == State.AWAITING_ATTESTATION:
        return f"Waiting for {case.ordering_clinician['name']} to sign."
    if case.state == State.AWAITING_NOTE:
        return f"Waiting for {case.ordering_clinician['name']} to write the note."
    if case.state != State.REVIEW:
        return "This case is not waiting for your review."
    if c["unmet"]:
        return (f"{c['unmet']} criterion is not met" if c["unmet"] == 1 else f"{c['unmet']} criteria are not met") + \
            f". Return it to {case.ordering_clinician['name']} for correction."
    if case.requirements and not case.requirements.get("matches"):
        return (f"{case.payer['name']} publishes a different version of this policy than the one approved here. "
                "Have the policy reviewed before approving.")
    if c["unknown"]:
        name = case.ordering_clinician["name"]
        return f"{name} needs to confirm {'the last one' if c['unknown'] == 1 else str(c['unknown']) + ' items'}."
    if case.draft and case.draft.get("out_of_date"):
        return (f"{case.ordering_clinician['name']} signed after the draft was edited. "
                "Read the draft again and save it before you approve.")
    return None


def _side(value: str | None) -> Literal["right", "left"] | None:
    return "right" if value == "right" else "left" if value == "left" else None


def _open_duplicate(cases: Iterable[Case], service_code: str, side: str | None) -> Case | None:
    """A case still open for the same service and side, which a new request would duplicate. The other knee is a
    request of its own, and only the payer's approval closes a case: a denial can be appealed, a return resubmitted."""
    return next((c for c in cases if c.service["code"] == service_code and c.side == side
                 and c.state != State.APPROVED), None)
