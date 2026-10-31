"""A case's audit trail in plain words, for the case page.

The audit log records every event: each tool call, each view of a case, each decision. Read raw, it is a few hundred
lines per case. Here consecutive tool calls in one workflow step become one line that names the tools, repeated
views by one person become one line with a count, and refused tool calls are marked so they stand out. Every line
keeps the sequence numbers and the hash of its last event, so an auditor can find it in the raw log.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from caduceus.audit.log import AuditEvent
from caduceus.auth import DEV_USERS
from caduceus.intake import ASKED_FOR, fields_in_words, listed

AGENT_IDS = {"caduceus-agent", "intake"}
STEP_WORDS = {"enrichment": "read the record, the referral packet and the payer's requirements",
              "drafting": "read the packet to write the draft", "review": "move the case to review",
              "submission": "send it to the payer", "follow_up": "read the payer's decision and file it",
              "intake": "read the faxed cover sheet"}
VIEWS = {"case.viewed": "Opened the case", "page.viewed": "Read a packet page",
         "attestation.viewed": "Opened the request to attest", "fax.viewed": "Opened the fax",
         "fax.patient_searched": "Looked the patient up in the chart",
         "ehr.record_viewed": "Opened the record the EHR shared"}
ROLE_WORDS = {"pa_specialist": "PA specialist", "clinician": "ordering clinician", "admin": "administrator",
              "auditor": "auditor", "agent_runtime": "agent"}


def who(event: AuditEvent) -> str:
    if event.actor in AGENT_IDS or event.role == "agent_runtime":
        return "Caduceus"
    person = DEV_USERS.get(event.actor)
    return person.name if person else event.actor


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def ehr(iss: str | None) -> str:
    return f"the EHR at {urlsplit(iss).hostname or iss}" if iss else "the EHR"


def sentence(e: AuditEvent, clinician: str) -> str:
    d = e.detail or {}
    match e.action:
        case "ehr.record_read":
            n = d.get("resources") or {}
            held = [plural(n.get("Condition", 0), "diagnosis", "diagnoses"),
                    plural(n.get("Procedure", 0), "procedure", "procedures"),
                    plural(n.get("Observation", 0), "measurement or result", "measurements and results"),
                    plural(d.get("notes", 0), "note", "notes")]
            return (f"Opened Caduceus from {ehr(d.get('iss'))} by a SMART launch. The EHR shared the patient’s "
                    f"record: {', '.join(held[:-1])} and {held[-1]}. Its access token was used for that one read and "
                    "kept nowhere.")
        case "case.received" if d.get("source") == "smart_launch":
            return (f"Started the case from the record {ehr(d.get('iss'))} shared: "
                    f"{plural(d.get('pages', 0), 'page', 'pages')}, its newest notes.")
        case "case.received" if d.get("from"):
            return f"Created the case from the checked fax: {plural(d.get('pages', 0), 'page', 'pages')}, the cover " \
                   "sheet first."
        case "case.received":
            return f"Received the referral, {plural(d.get('pages', 0), 'page', 'pages')}."
        case "fax.received":
            return f"Received a referral by fax, {plural(d.get('pages', 0), 'page', 'pages')}."
        case "fax.read":
            n = d.get("to_check", 0)
            return "Read the cover sheet with OCR, inside Mesa Verde’s systems. " + (
                f"{plural(n, 'field', 'fields')} went to a person to check." if n else "Every field was read clearly.")
        case "fax.confirmed":
            corrected = fields_in_words(d.get("corrected", []))
            return f"Checked the fax against the page{f', corrected {corrected},' if corrected else ''} and created " \
                   "the case."
        case "criteria.evaluated":
            c = d.get("counts", {})
            return (f"Checked the record against the coverage policy: {c.get('met', 0)} met, "
                    f"{c.get('unknown', 0)} not found, {c.get('unmet', 0)} not met.")
        case "draft.generated":
            how = {"external": "with the model, from de-identified facts", "deid_in_vpc": "with the in-VPC model",
                   "template": "from the template"}.get(d.get("route"), "")
            why = " after the clinician signed" if d.get("reason") == "attestation signed" else ""
            return f"Wrote the draft {how}{why}.".replace("  ", " ")
        case "draft.out_of_date":
            return "Marked the edited draft out of date, because the clinician signed after the edit."
        case "draft.edited":
            return "Edited the draft."
        case "attestation.requested":
            return f"Asked {clinician} to confirm {plural(len(d.get('criteria', [])), 'item', 'items')}."
        case "attestation.signed":
            answers = list((d.get("answers") or {}).values())
            return f"Signed, answering {', '.join(answers) or 'the questions'}."
        case "case.approved":
            return "Approved the request. It waited 30 seconds, so it could be undone, before it was sent."
        case "case.recalled":
            return {"reply": "Recalled the note before it was sent. It waits to be sent again.",
                    "appeal": "Recalled the appeal before it was sent. It waits to be sent again."}.get(
                d.get("kind"), "Recalled the request before it was sent. It is back in review.")
        case "action.undone":
            return f"Undid their last step ({d.get('undone', 'an action').replace('.', ' ')})."
        case "case.returned":
            return "Returned the case to the clinic for a correction."
        case "clinician.reminded":
            return f"Reminded {clinician} to {'write the note' if d.get('kind') == 'note' else 'sign'}."
        case "case.resubmitted":
            return "Marked that the clinic resubmitted it. Caduceus prepares it again from the start."
        case "note.requested":
            return f"Asked {clinician} for the note the payer wants."
        case "note.signed":
            return "Wrote and signed a note for the payer, added to the chart as an addendum."
        case "note.declined":
            return "Said they cannot document what the payer asked for. Nothing was sent."
        case "reply.approved":
            return "Approved sending the note. It waited 30 seconds before it was sent."
        case "dispatch.error":
            return "Sending failed; it will be tried again."
        case "fax.clinic_asked":
            to = d.get("to") or "the referring clinic"
            if d.get("kind") == "resend":
                return f"Asked {to} to fax the referral again, because the copy was too poor to read."
            asked = [ASKED_FOR.get(k, k) for k in d.get("missing", [])] or ["what was missing"]
            return (f"Asked {to} for {listed(asked)}. The request was shown to send by fax or phone; "
                    "Caduceus sent nothing.")
        case "fax.clinic_replied":
            return "Marked that the clinic sent what was asked for."
    return e.action.replace(".", " ").capitalize() + "."


def timeline(events: list[AuditEvent], clinician: str, tz: ZoneInfo, now: datetime) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def add(e: AuditEvent, what: str, kind: str, tools: list[str] | None = None) -> None:
        at = datetime.fromisoformat(e.ts).astimezone(tz)
        day = "" if at.date() == now.astimezone(tz).date() else f"{at:%b} {at.day}, "
        entries.append({"first_seq": e.seq, "last_seq": e.seq, "hash": e.hash[:12], "who": who(e),
                        "role": ROLE_WORDS.get(e.role, e.role), "what": what, "kind": kind, "tools": tools or [],
                        "at": f"{day}{at.hour % 12 or 12}:{at:%M:%S} {'am' if at.hour < 12 else 'pm'}",
                        "count": 1, "_key": None})

    for e in events:
        last = entries[-1] if entries else None
        if e.action == "tool.call":
            step = e.detail.get("step", "")
            key = ("tools", e.actor, step)
            if last and last["_key"] == key:
                last["tools"].append(e.detail.get("tool", "?"))
                last["last_seq"], last["hash"] = e.seq, e.hash[:12]
                last["what"] = f"Used {plural(len(last['tools']), 'tool', 'tools')} to {STEP_WORDS.get(step, step)}."
                continue
            add(e, f"Used 1 tool to {STEP_WORDS.get(step, step)}.", "agent", [e.detail.get("tool", "?")])
            entries[-1]["_key"] = key
        elif e.action in ("tool.forbidden", "tool.not_allowed"):
            why = ("that role may not call it" if e.action == "tool.forbidden"
                   else f"it is not allowed during {e.detail.get('step', 'that step')}")
            add(e, f"Was refused {e.detail.get('tool', 'a tool')}: {why}.", "refused")
        elif e.action in VIEWS:
            key = ("views", e.actor, e.action)
            if last and last["_key"] == key:
                last["count"] += 1
                last["last_seq"], last["hash"] = e.seq, e.hash[:12]
                last["what"] = f"{VIEWS[e.action]} ({last['count']} times)."
                continue
            add(e, f"{VIEWS[e.action]}.", "view")
            entries[-1]["_key"] = key
        else:
            add(e, sentence(e, clinician), "agent" if who(e) == "Caduceus" else "person")
    for entry in entries:
        entry.pop("_key")
    return entries
