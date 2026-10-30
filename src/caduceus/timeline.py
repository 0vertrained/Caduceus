"""A case's audit trail in plain words, for the case page.

The audit log records every event: each tool call, each view of a case, each decision. Read raw, it is a few hundred
lines per case. Here consecutive tool calls in one workflow step become one line that names the tools, repeated
views by one person become one line with a count, and refused tool calls are marked so they stand out. Every line
keeps the sequence numbers and the hash of its last event, so an auditor can find it in the raw log.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from caduceus.audit.log import AuditEvent
from caduceus.auth import DEV_USERS

AGENT_IDS = {"caduceus-agent", "intake"}
STEP_WORDS = {"enrichment": "read the patient's record and the referral packet",
              "drafting": "read the packet to write the draft", "review": "move the case to review",
              "submission": "send it to the payer", "follow_up": "read the payer's decision and file it"}
VIEWS = {"case.viewed": "Opened the case", "page.viewed": "Read a packet page",
         "attestation.viewed": "Opened the request to attest"}
ROLE_WORDS = {"pa_specialist": "PA specialist", "clinician": "ordering clinician", "admin": "administrator",
              "auditor": "auditor", "agent_runtime": "agent"}


def who(event: AuditEvent) -> str:
    if event.actor in AGENT_IDS or event.role == "agent_runtime":
        return "Caduceus"
    person = DEV_USERS.get(event.actor)
    return person.name if person else event.actor


def plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def sentence(e: AuditEvent, clinician: str) -> str:
    d = e.detail or {}
    match e.action:
        case "case.received":
            return f"Received the referral, {plural(d.get('pages', 0), 'page', 'pages')}."
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
        case "action.undone":
            return f"Undid their last step ({d.get('undone', 'an action').replace('.', ' ')})."
        case "case.returned":
            return "Returned the case to the clinic for a correction."
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
