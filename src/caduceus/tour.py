"""The public demo's guided tour: where the headline case stands, and who should act next.

The tour follows one case from the referral to the payer's approval. Each step names the person to act as, so a
visitor can switch roles with one click instead of working out the cast. It reads the visitor's own copy of the
cases, so it always describes what they see.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from caduceus.auth import DEV_USERS
from caduceus.cases import State
from caduceus.drafting import patient_name

if TYPE_CHECKING:
    from caduceus.app_factory import Services
    from caduceus.presenter import Presenter

HEADLINE = "2026-1043"
STEPS = 8


def tour(svc: Services, presenter: Presenter) -> dict[str, Any] | None:
    case = svc.cases.get(HEADLINE)
    if case is None:
        return None
    patient = patient_name(presenter.patient(case))
    clin, payer = case.ordering_clinician["name"], case.payer["name"]
    reply = case.reply or {}
    counts = case.effective_counts()
    sub = case.submission or {}
    case_path, attest_path = f"/cases/{case.id}", f"/attest/{case.id}"

    def step(n: int, actor: str | None, text: str, path: str | None = None, waiting: bool = False,
             ended: bool = False, done: bool = False) -> dict[str, Any]:
        return {"step": n, "steps": STEPS, "actor": actor, "actor_name": DEV_USERS[actor].name if actor else None,
                "text": text, "path": path, "waiting": waiting, "ended": ended, "done": done,
                "case_id": case.id}

    if case.state == State.REVIEW and counts["unmet"]:
        return step(3, None, f"{clin} said this was not done, so the request cannot go. Start over to try the "
                             "other answer.", ended=True)
    if case.state == State.REVIEW and counts["unknown"]:
        return step(1, "dolores", f"Open {patient}'s case. One coverage criterion is not in her record, so send it "
                                  f"to {clin} to confirm.", case_path)
    if case.state == State.AWAITING_ATTESTATION:
        return step(2, "dr.reyes", f"As {clin}, answer the question and sign. On a phone this is one screen.",
                    attest_path)
    if case.state == State.REVIEW:
        return step(3, "dolores", "Every criterion is met now. Read the draft, and how it was written, then approve "
                                  "and send it.", case_path)
    if case.state == State.SUBMITTED and not sub.get("sent") and sub.get("kind") != "reply":
        return step(4, "dolores", f"The request waits 30 seconds so you can undo it, then goes to {payer}.",
                    case_path, waiting=True)
    if case.state == State.PENDED and reply.get("declined_by"):
        return step(5, None, f"{clin} could not document it, so nothing more goes to the payer. Start over to try "
                             "again.", ended=True)
    if case.state == State.PENDED and reply.get("note"):
        return step(7, "dolores", f"Read {clin}'s note, then send it to {payer}.", case_path)
    if case.state == State.PENDED:
        return step(5, "dolores", f"{payer} wants the note behind {clin}'s answer. Ask her for it.", case_path)
    if case.state == State.AWAITING_NOTE:
        return step(6, "dr.reyes", f"As {clin}, write the note in your own words and sign it.", attest_path)
    if case.state == State.SUBMITTED:
        return step(8, "dolores", f"The note waits 30 seconds, then goes to {payer}.", case_path, waiting=True)
    if case.state == State.APPROVED:
        return step(STEPS, None, f"{payer} approved it. Open the case history to see every step, who took it, and "
                                 "the check that nothing was changed afterward.", case_path, done=True)
    return step(1, None, "This case left the tour's path. Start over to try again.", ended=True)
