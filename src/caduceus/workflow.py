"""The case workflow: what the agent does, what people do, and the rules between them.

Agent steps (enrichment, determination, drafting) run as the agent-runtime identity through the tool gateway.
Human steps (request attestation, sign, approve, return, undo) run as the signed-in person and are checked here.
Approval puts the submission in an outbox for the 30-second undo window; only then does the agent send it.

When the payer pends a request and asks for documentation, the same pattern repeats: the PA specialist asks the
ordering clinician, the clinician writes and signs the note themselves (or says they cannot document it), the
specialist approves the reply, and the agent sends it after the undo window.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from caduceus.audit.log import AuditLog, sha256_of
from caduceus.auth import AGENT, Principal, Role
from caduceus.cases import Attestation, Case, CaseRepository, LastAction, State
from caduceus.criteria.dsl import CriteriaSet
from caduceus.criteria.engine import Engine
from caduceus.drafting import Drafter
from caduceus.fhir.store import FhirStore
from caduceus.payer import MockPayer, build_appeal_bundle, build_reply_bundle, build_request_bundle
from caduceus.tools import ToolGateway, build_gateway

UNDO_SECONDS = 30
NOTE_LIMIT = 4000  # characters; a payer's question is answered in a paragraph or two
SNAPSHOT_FIELDS = ("state", "attestation_requested", "attestation_requested_at", "attestation_requested_by",
                   "attestations", "returned_reason", "submission", "payer_response", "draft", "reply", "appeal")


class ActionRefused(Exception):
    """A person tried something the case's state or their role does not allow. The message says why."""


class Workflow:
    def __init__(self, cases: CaseRepository, audit: AuditLog, store: FhirStore, library: dict[str, CriteriaSet],
                 engine: Engine, drafter: Drafter, payer: MockPayer,
                 clock: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self.cases, self.audit, self.store, self.library = cases, audit, store, library
        self.engine, self.drafter, self.payer, self.now = engine, drafter, payer, clock
        self._dispatch_lock = threading.Lock()  # one dispatcher at a time: a request is never sent twice
        self.gateway: ToolGateway = build_gateway(audit, store, library, self._set_state, self._submit,
                                                  self._payer_status, self._respond, self._appeal,
                                                  self.payer.coverage_requirements)

    # Agent ---------------------------------------------------------------------------------------------

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
        det = self.engine.evaluate(self.library[case.criteria_set], record, date.fromisoformat(case.as_of))
        case.determination, case.checked_at = det.to_dict(), self.now()
        # The payer's published policy must be the one approved here; if the payer changed it, a person reviews it.
        case.requirements = required | {"matches": required["policy"]["sha256"] == det.content_sha256,
                                        "checked_at": self.now().isoformat()}
        self.audit.append(AGENT.id, AGENT.role, "criteria.evaluated", cid,
                          {"set": det.criteria_set, "set_sha256": det.content_sha256, "overall": det.overall,
                           "counts": det.counts, "result_sha256": sha256_of(det.to_dict())})

        self._draft(case, record.patient, reason="prepared")
        case = self.cases.save(case)
        gw.call(AGENT, cid, "review", "tickets.set_state", case_id=cid, state=State.REVIEW.value,
                reason="prepared for review")
        return self._get(cid)

    def _draft(self, case: Case, patient: dict, reason: str) -> None:
        """Write the draft from the current (attestation-aware) results. Runs as the agent identity."""
        self.gateway.call(AGENT, case.id, "drafting", "documents.list_packet", packet=case.packet)
        draft = self.drafter.draft(case, patient)
        draft["generated_at"] = self.now().isoformat()
        case.draft = draft
        self.audit.append(AGENT.id, AGENT.role, "draft.generated", case.id,
                          {"reason": reason, "route": draft["route"], "model": draft.get("model"),
                           "deid_removed": draft.get("deid_removed"), "text_sha256": sha256_of(draft["text"])})

    def _refresh_draft_after_attestation(self, case: Case) -> None:
        """The outgoing letter must match the signed answers. A person's edits are never overwritten:
        an edited draft is marked out of date, and approval waits until someone re-reads and saves it."""
        if case.draft and case.draft.get("edited_by"):
            case.draft = case.draft | {"out_of_date": True}
            self.audit.append(AGENT.id, AGENT.role, "draft.out_of_date", case.id, {"reason": "attestation signed"})
            return
        self._draft(case, self.store.record(case.patient_id, case.id).patient, reason="attestation signed")

    def dispatch_due(self) -> list[str]:
        """Send approved requests whose undo window has passed. Safe to call often, from any thread."""
        with self._dispatch_lock:
            return self._dispatch_due_locked()

    def _dispatch_due_locked(self) -> list[str]:
        sent = []
        for case in self.cases.all():
            sub = case.submission or {}
            if case.state == State.SUBMITTED and not sub.get("sent") and \
                    datetime.fromisoformat(sub["send_after"]) <= self.now():
                tool = {"reply": "payer.respond", "appeal": "payer.appeal"}.get(sub.get("kind"), "payer.submit")
                result = self.gateway.call(AGENT, case.id, "submission", tool, case_id=case.id,
                                           approval_seq=sub["approval_seq"])
                decision = self.gateway.call(AGENT, case.id, "follow_up", "payer.status",
                                             submission_id=result["id"])
                case = self._get(case.id)
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
                sent.append(case.id)
        return sent

    # People --------------------------------------------------------------------------------------------

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
        self.audit.append(who.id, who.role, "attestation.requested", case_id,
                          {"criteria": unknown, "clinician": case.ordering_clinician["id"]})
        return self.cases.save(case)

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

    def approve(self, who: Principal, case_id: str) -> Case:
        self._require(who, Role.PA_SPECIALIST)
        case = self._get(case_id)
        if not case.ready_to_approve:
            raise ActionRefused(approve_blocked_reason(case) or "This case cannot be approved yet.")
        event = self.audit.append(who.id, who.role, "case.approved", case_id,
                                  {"draft_sha256": sha256_of(case.draft["text"] if case.draft else "")})
        self._remember(case, who, "case.approved")
        case.state = State.SUBMITTED
        case.submission = {"approval_seq": event.seq, "approved_by": who.name, "approved_by_id": who.id,
                           "approved_at": self.now().isoformat(), "sent": False,
                           "send_after": (self.now() + timedelta(seconds=UNDO_SECONDS)).isoformat()}
        return self.cases.save(case)

    def request_note(self, who: Principal, case_id: str) -> Case:
        """The payer pended the request and asked for documentation. Ask the ordering clinician to write it."""
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

    def decline_note(self, who: Principal, case_id: str) -> Case:
        """The clinician cannot document what the payer asked for. Nothing is sent; the specialist decides next."""
        case = self._clinician_note_case(who, case_id)
        self._remember(case, who, "note.declined")
        case.reply = (case.reply or {}) | {"declined_by": who.name, "declined_at": self.now().isoformat()}
        case.state = State.PENDED
        self.audit.append(who.id, who.role, "note.declined", case_id)
        return self.cases.save(case)

    def approve_reply(self, who: Principal, case_id: str) -> Case:
        """Approve sending the signed note. Like a request, it waits in the outbox for the undo window."""
        self._require(who, Role.PA_SPECIALIST)
        case = self._get(case_id)
        reply = case.reply or {}
        if case.state != State.PENDED:
            raise ActionRefused("The payer is not waiting for anything on this case.")
        if not reply.get("note"):
            raise ActionRefused(f"{case.ordering_clinician['name']} could not document this, so there is no note "
                                "to send." if reply.get("declined_by")
                                else f"Ask {case.ordering_clinician['name']} for the note first.")
        event = self.audit.append(who.id, who.role, "reply.approved", case_id,
                                  {"note_sha256": sha256_of(reply["note"]["text"])})
        self._remember(case, who, "reply.approved")
        case.state = State.SUBMITTED
        case.submission = {"kind": "reply", "approval_seq": event.seq, "approved_by": who.name,
                           "approved_by_id": who.id, "approved_at": self.now().isoformat(), "sent": False,
                           "send_after": (self.now() + timedelta(seconds=UNDO_SECONDS)).isoformat()}
        return self.cases.save(case)

    def _draft_appeal(self, case_id: str) -> None:
        """After a denial the agent drafts the appeal from the payer's reason, the policy and the record."""
        case = self._get(case_id)
        self.gateway.call(AGENT, case.id, "drafting", "documents.list_packet", packet=case.packet)
        appeal = self.drafter.appeal(case, self.store.record(case.patient_id, case.id).patient)
        case.appeal = appeal | {"generated_at": self.now().isoformat()}
        self.audit.append(AGENT.id, AGENT.role, "appeal.drafted", case.id,
                          {"route": appeal["route"], "model": appeal.get("model"),
                           "text_sha256": sha256_of(appeal["text"])})
        self.cases.save(case)

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

    def approve_appeal(self, who: Principal, case_id: str) -> Case:
        """Approve sending the appeal. Like a request, it waits in the outbox for the undo window."""
        self._require(who, Role.PA_SPECIALIST)
        case = self._get(case_id)
        if case.state != State.DENIED or not case.appeal:
            raise ActionRefused("There is no denial to appeal on this case.")
        event = self.audit.append(who.id, who.role, "appeal.approved", case_id,
                                  {"appeal_sha256": sha256_of(case.appeal["text"])})
        self._remember(case, who, "appeal.approved")
        case.state = State.SUBMITTED
        case.submission = {"kind": "appeal", "approval_seq": event.seq, "approved_by": who.name,
                           "approved_by_id": who.id, "approved_at": self.now().isoformat(), "sent": False,
                           "send_after": (self.now() + timedelta(seconds=UNDO_SECONDS)).isoformat()}
        return self.cases.save(case)

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

    def undo(self, who: Principal, case_id: str) -> Case:
        case = self._get(case_id)
        last = case.last_action
        if last is None or last.actor != who.id:
            raise ActionRefused("There is nothing of yours to undo on this case.")
        if self.now() > last.undo_until:
            raise ActionRefused("The 30 seconds to undo have passed. Use the case history to recall it.")
        if case.sent_since_last_action:
            raise ActionRefused("This has already reached the payer.")
        restored = Case.model_validate(case.model_dump(mode="json") | last.prior | {"last_action": None})
        self.audit.append(who.id, who.role, "action.undone", case_id, {"undone": last.action})
        return self.cases.save(restored)

    # Internals -----------------------------------------------------------------------------------------

    def _get(self, case_id: str) -> Case:
        case = self.cases.get(case_id)
        if case is None:
            raise KeyError(case_id)
        return case

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
        prior = {f: case.model_dump(mode="json")[f] for f in SNAPSHOT_FIELDS}
        case.last_action = LastAction(action=action, actor=who.id, at=self.now(),
                                      undo_until=self.now() + timedelta(seconds=UNDO_SECONDS), prior=prior)

    def _set_state(self, case_id: str, state: str, reason: str) -> None:
        case = self._get(case_id)
        case.state = State(state)
        self.cases.save(case)

    def _outbox(self, case_id: str, approval_seq: int, action: str, kind: str | None) -> tuple[Case, Any]:
        """What a payer tool may send: only what a person approved (recorded in the audit log), and only while that
        approval is the one waiting in the outbox. An approval that was undone cannot be replayed."""
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
        bundle = build_request_bundle(case)
        result = self.payer.submit(bundle, case.payer["name"])
        case.submission = (case.submission or {}) | {"sent": True, "sent_at": self.now().isoformat(),
                                                     "submission_id": result["id"]}
        self.cases.save(case)
        return result

    def _respond(self, case_id: str, approval_seq: int) -> dict[str, Any]:
        """The payer.respond tool. Sends the note a person approved, exactly as it was when they approved it."""
        case, approval = self._outbox(case_id, approval_seq, "reply.approved", "reply")
        note = (case.reply or {}).get("note") or {}
        if sha256_of(note.get("text", "")) != approval.detail.get("note_sha256"):
            raise ActionRefused("The note changed after it was approved.")
        result = self.payer.respond(build_reply_bundle(case), case.payer["name"], case.payer_response["id"])
        case.submission = (case.submission or {}) | {"sent": True, "sent_at": self.now().isoformat(),
                                                     "submission_id": result["id"]}
        self.cases.save(case)
        return result

    def _appeal(self, case_id: str, approval_seq: int) -> dict[str, Any]:
        """The payer.appeal tool. Sends the appeal a person approved, exactly as it was when they approved it."""
        case, approval = self._outbox(case_id, approval_seq, "appeal.approved", "appeal")
        if sha256_of((case.appeal or {}).get("text", "")) != approval.detail.get("appeal_sha256"):
            raise ActionRefused("The appeal changed after it was approved.")
        result = self.payer.appeal(build_appeal_bundle(case), case.payer["name"], case.payer_response["id"])
        case.submission = (case.submission or {}) | {"sent": True, "sent_at": self.now().isoformat(),
                                                     "submission_id": result["id"]}
        self.cases.save(case)
        return result

    def _payer_status(self, submission_id: str) -> dict[str, Any]:
        return self.payer.status(submission_id)


def approve_blocked_reason(case: Case) -> str | None:
    c = case.effective_counts()
    if case.state == State.AWAITING_ATTESTATION:
        return f"Waiting for {case.ordering_clinician['name']} to sign."
    if case.state == State.AWAITING_NOTE:
        return f"Waiting for {case.ordering_clinician['name']} to write the note."
    if case.state != State.REVIEW:
        return "This case is not waiting for your review."
    if c["unmet"]:
        return f"{c['unmet']} criterion is not met." if c["unmet"] == 1 else f"{c['unmet']} criteria are not met."
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
