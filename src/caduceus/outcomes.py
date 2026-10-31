"""What the queue's work adds up to, computed from the audit log and the cases: never estimated, never stored.

Each figure says what it counts. In the public demo the numbers describe the visitor's own copy, so they move as the
visitor works; in a deployment they would be read per day or week from the same log.
"""

from __future__ import annotations

from datetime import datetime
from statistics import median
from typing import Any

from caduceus.audit.log import AuditEvent
from caduceus.cases import Case, Fax, State

HUMAN_ACTIONS = {"attestation.requested", "attestation.signed", "case.approved", "case.returned", "draft.edited",
                 "note.requested", "note.signed", "note.declined", "reply.approved", "appeal.edited",
                 "appeal.approved", "fax.confirmed", "action.undone", "fax.clinic_asked", "fax.clinic_replied",
                 "case.recalled", "case.resubmitted"}


def pct(n: int, d: int) -> str:
    return f"{round(100 * n / d)}%" if d else "none yet"


def minutes(values: list[float]) -> str:
    if not values:
        return "none yet"
    m = median(values)
    return "under a minute" if m < 1 else f"{round(m)} minute{'s' if round(m) != 1 else ''}"


def outcomes(cases: list[Case], events: list[AuditEvent], faxes: list[Fax]) -> list[dict[str, Any]]:
    results = [r for c in cases for r in (c.determination or {}).get("results", [])]
    found = sum(r["status"] == "met" for r in results)
    unknown = sum(r["status"] == "unknown" for r in results)
    calls = [e for e in events if e.action == "tool.call"]
    refused = [e for e in events if e.action in ("tool.forbidden", "tool.not_allowed")]
    drafts = [e for e in events if e.action in ("draft.generated", "appeal.drafted")]
    by_model = sum(e.detail.get("route") in ("external", "deid_in_vpc") for e in drafts)
    removed = sum(sum((c.draft or {}).get("deid_removed", {}).values()) for c in cases)
    replayed = sum(bool(((c.draft or {}).get("sent") or {}).get("cached")) for c in cases)

    at = {(e.case_id, e.action): datetime.fromisoformat(e.ts) for e in events}
    waits = [(at[(c, "attestation.signed")] - t).total_seconds() / 60 for (c, a), t in at.items()
             if a == "attestation.requested" and (c, "attestation.signed") in at]
    sent = [c for c in cases if c.sent_at is not None]
    decided = [c for c in sent if c.decided_at is not None]
    covered = [c for c in decided if (c.requirements or {}).get("decision_timeframe")]
    within = [c for c in covered if (c.decided_at - c.sent_at).days < 7]  # type: ignore[operator]
    pended = {e.case_id for e in events if e.action == "tool.call" and e.detail.get("tool") == "payer.respond"}
    appealed = {e.case_id for e in events if e.action == "appeal.approved"}
    first_time = [c for c in sent if c.state == State.APPROVED and c.id not in pended | appealed]
    touches = {}
    for e in events:
        if e.action in HUMAN_ACTIONS and e.case_id:
            touches[e.case_id] = touches.get(e.case_id, 0) + 1
    sent_touches = [touches.get(c.id, 0) for c in sent]
    read = [f for f in faxes if f.reading]
    fields = [x for f in read for x in f.reading["fields"]]  # type: ignore[index]
    corrected = sum(len(e.detail.get("corrected", [])) for e in events if e.action == "fax.confirmed")

    return [
        {"title": "What the agent did", "rows": [
            ("Cases prepared", str(len([c for c in cases if c.determination])),
             "Record read, policy checked, draft written."),
            ("Criteria checked", str(len(results)), "Against each payer's approved policy, version-locked."),
            ("Found in the record", pct(found, len(results)), "Each with the sentence and page it came from."),
            ("Sent to a clinician instead of guessed", str(unknown),
             "A criterion with no evidence is never marked met."),
            ("Tool calls", str(len(calls)), "Every one role-checked, step-checked and logged."),
            ("Tool calls refused", str(len(refused)), "Outside their workflow step or the caller's role."),
        ]},
        {"title": "What people did", "rows": [
            ("Decisions people made", str(sum(touches.values())),
             "Approvals, signatures, notes, edits, returns, undos."),
            ("Decisions per request sent", f"{median(sent_touches):g}" if sent_touches else "none yet",
             "Median, for requests that reached a payer."),
            ("Clinician answer time", minutes(waits), "Median, from asking to signing."),
        ]},
        {"title": "What the payers said", "rows": [
            ("Requests sent", str(len(sent)), "Each approved by a person, after a 30-second undo window."),
            ("Approved the first time", pct(len(first_time), len(sent)), "With no question asked and no appeal."),
            ("Questions answered", str(len(pended)), "Each with a note the ordering clinician wrote and signed."),
            ("Denials appealed", str(len(appealed)),
             "Each appeal drafted from the policy and record, approved by a person."),
            ("Decided within the CMS limit", pct(len(within), len(covered)),
             "Seven calendar days, for the plans the rule covers."),
        ]},
        {"title": "Where patient information went", "rows": [
            ("Drafts written by a model", f"{by_model} of {len(drafts)}",
             "The rest from the template, inside Caduceus."),
            ("Identifiers removed before sending", str(removed), "Names, dates and numbers, put back only after."),
            ("Replies replayed, not requested again", str(replayed),
             "Identical de-identified requests are answered once."),
            ("Identifiers that reached the model", "0", "Checked on every change by the leakage suite in CI."),
            ("Records read from an EHR", str(sum(e.action == "ehr.record_read" for e in events)),
             "By SMART launch, over FHIR. Each access token was used for one read and kept nowhere."),
        ]},
        {"title": "Faxes read", "rows": [
            ("Faxes read inside the boundary", str(len(read)), "By OCR in the container; pages never leave."),
            ("Fields sent to a person", pct(sum(1 for x in fields if x.get("problem")), len(fields)),
             "Below 90% confidence, or not valid."),
            ("Fields people corrected", str(corrected), "Before any case was created from them."),
            ("Faxes sent back to the clinic", str(sum(e.action == "fax.clinic_asked" for e in events)),
             "Something was missing or unreadable, so a person asked the clinic instead of guessing."),
        ]},
    ]
