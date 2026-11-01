"""Prior-authorization bundles and a mock payer.

The bundles are a simplified subset of the Da Vinci PAS guide: a Bundle whose first entry is a Claim with
`use = preauthorization`. A reply or an appeal is a further Claim related to the submission it answers. Decisions use
PAS's X12 review-action codes: A1 certified, A3 not certified, A4 pended.
"""

from __future__ import annotations

import base64
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from caduceus.cases import Case

REVIEW_ACTION = "https://codesystem.x12.org/005010/306"

# The plans CMS-0057-F covers owe a decision within 7 calendar days, or 72 hours when expedited, with a specific
# reason for any denial. Original Medicare is not one of them.
PLAN_TYPES = {"Humana Medicare Advantage": "Medicare Advantage",
              "UnitedHealthcare Medicare Advantage": "Medicare Advantage",
              "Medicaid managed care": "Medicaid managed care", "Medicare": "Original Medicare"}
RULE_PLANS = {"Medicare Advantage", "Medicaid managed care"}
STANDARD_DECISION_DAYS, EXPEDITED_DECISION_HOURS = 7, 72
QUESTIONNAIRE = "http://mesaverde.example/fhir/Questionnaire"
SERVICE_SYSTEMS = {"cpt": "http://www.ama-assn.org/go/cpt",
                   "hcpcs": "https://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"}


def questionnaire_response(case: Case) -> dict[str, Any]:
    """The payer's documentation questions answered from the record (DTR-style): one item per criterion, each
    with its finding and the evidence or attestation behind it."""
    return {
        "resourceType": "QuestionnaireResponse", "id": f"dtr-{case.id}", "status": "completed",
        "questionnaire": f"{QUESTIONNAIRE}/{case.criteria_set}",
        "subject": {"reference": f"Patient/{case.patient_id}"},
        "item": [{"linkId": r["criterion_id"], "text": r["text"], "answer": [
            {"valueCoding": {"system": "http://mesaverde.example/criterion-status", "code": r["status"]}},
            {"valueString": r.get("explanation", "")}]} for r in case.effective_results()],
    }


def priority(case: Case) -> dict[str, Any]:
    """FHIR process priority: "stat" asks for an expedited decision."""
    return {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/processpriority",
                        "code": "stat" if case.urgency == "expedited" else "normal"}]}


def service_item(case: Case) -> dict[str, Any]:
    """The requested service as the claim's one item. A service for one side of the body (a knee replacement) names
    it with the HCPCS level II modifier, LT or RT, as payers expect it."""
    item: dict[str, Any] = {"sequence": 1, "productOrService": {"coding": [{
        "system": SERVICE_SYSTEMS[case.service["system"]], "code": case.service["code"],
        "display": case.service["display"]}]}}
    side = case.side
    if side in ("left", "right"):
        item["modifier"] = [{"coding": [{"system": SERVICE_SYSTEMS["hcpcs"], "code": side[0].upper() + "T",
                                         "display": f"{side.capitalize()} side"}]}]
    return item


def _bundle(case: Case, entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"resourceType": "Bundle", "type": "collection",
            "identifier": {"system": "http://mesaverde.example/pa-request", "value": case.id},
            "timestamp": datetime.now(UTC).isoformat(timespec="seconds"), "entry": entries}


def _claim(case: Case, claim_id: str, **parts: Any) -> dict[str, Any]:
    """The case's preauthorization Claim, with `parts` (related claims, supporting information) before its item."""
    return {"resourceType": "Claim", "id": claim_id, "status": "active", "use": "preauthorization",
            "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type",
                                 "code": "professional"}]},
            "patient": {"reference": f"Patient/{case.patient_id}"},
            "created": datetime.now(UTC).date().isoformat(),
            "insurer": {"display": case.payer["name"]},
            "provider": {"reference": f"Practitioner/{case.ordering_clinician['id']}",
                         "display": case.ordering_clinician["name"]},
            "priority": priority(case), **parts, "item": [service_item(case)]}


def _related(case: Case) -> list[dict[str, Any]]:
    """The submission a reply or an appeal answers."""
    return [{"claim": {"identifier": {"system": "http://mesaverde.example/payer-submission",
                                      "value": (case.payer_response or {})["id"]}},
             "relationship": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/related-claim-relationship",
                                          "code": "prior"}]}}]


def build_request_bundle(case: Case) -> dict[str, Any]:
    claim_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"caduceus/claim/{case.id}"))
    results = case.effective_results()
    qr = questionnaire_response(case)
    info = [{"sequence": i, "category": {"text": r["text"]},
             "valueString": r.get("explanation", "") + (f" Attested by {r['attested_by']}."
                                                        if r.get("attested_by") else "")}
            for i, r in enumerate(results, start=1)]
    info.append({"sequence": len(results) + 1, "category": {"text": "Documentation questionnaire"},
                 "valueReference": {"reference": f"QuestionnaireResponse/{qr['id']}"}})
    return _bundle(case, [{"fullUrl": f"urn:uuid:{claim_id}", "resource": _claim(case, claim_id, supportingInfo=info)},
                          {"fullUrl": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, qr['id'])}", "resource": qr}])


def build_appeal_bundle(case: Case) -> dict[str, Any]:
    """A request for reconsideration: the original request's claim, related to the denied submission, with the
    appeal letter a person approved as an attachment."""
    bundle = build_request_bundle(case)
    claim = bundle["entry"][0]["resource"]
    claim["id"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"caduceus/claim/{case.id}/appeal"))
    claim["related"] = _related(case)
    claim["supportingInfo"].append({
        "sequence": len(claim["supportingInfo"]) + 1, "category": {"text": "Appeal of the denial"},
        "valueAttachment": {"contentType": "text/plain; charset=utf-8", "title": "Request for reconsideration",
                            "data": base64.b64encode((case.appeal or {})["text"].encode()).decode()}})
    return bundle


def build_reply_bundle(case: Case) -> dict[str, Any]:
    """The documentation a payer asked for, as an update to the original request."""
    note = (case.reply or {})["note"]
    claim_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"caduceus/claim/{case.id}/reply/{note['signed_at']}"))
    info = [{"sequence": 1, "category": {"text": "Documentation the payer requested"},
             "valueAttachment": {"contentType": "text/plain; charset=utf-8",
                                 "data": base64.b64encode(note["text"].encode()).decode(),
                                 "title": f"Addendum by {note['author']}", "creation": note["signed_at"]}}]
    return _bundle(case, [{"fullUrl": f"urn:uuid:{claim_id}",
                           "resource": _claim(case, claim_id, related=_related(case), supportingInfo=info)}])


class MockPayer:
    """Approves complete requests. Payers in `pend_attested` ask for the note behind any attested criterion, and
    approve once a reply carries a note."""

    def __init__(self, pend_attested: frozenset[str] = frozenset({"Humana Medicare Advantage"}),
                 published: dict[str, str] | None = None,
                 strict_sleep_apnea: frozenset[str] = frozenset({"UnitedHealthcare Medicare Advantage"}),
                 review_seconds: float = 0.0):
        self._pend_attested = pend_attested
        self._review = review_seconds  # before a decision can be read; half when expedited
        self._published = published or {}  # policy key -> content hash, as each payer publishes it
        self._strict = strict_sleep_apnea  # payers whose first-level review applies only the AHI 15 threshold
        self._submissions: dict[str, dict[str, Any]] = {}

    def coverage_requirements(self, payer_name: str, service_code: str, policy: str) -> dict[str, Any]:
        """What a coverage-requirements service (Da Vinci CRD) answers when an order is placed."""
        plan = PLAN_TYPES.get(payer_name, "Commercial")
        covered_by_rule = plan in RULE_PLANS
        return {"payer": payer_name, "plan_type": plan, "service": service_code, "prior_auth_required": True,
                "policy": {"key": policy, "sha256": self._published.get(policy)},
                "questionnaire": f"{QUESTIONNAIRE}/{policy}",
                "decision_timeframe": {"standard_days": STANDARD_DECISION_DAYS,
                                       "expedited_hours": EXPEDITED_DECISION_HOURS} if covered_by_rule else None}

    def _record(self, submission_id: str, decision: dict[str, Any], claim: dict[str, Any],
                now: datetime | None) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        stat = any(c.get("code") == "stat" for c in (claim.get("priority") or {}).get("coding", []))
        wait = timedelta(seconds=self._review / (2 if stat else 1))
        self._submissions[submission_id] = {"id": submission_id, "submitted_at": now.isoformat(),
                                            "decision": decision, "system": REVIEW_ACTION,
                                            "priority": "stat" if stat else "normal",
                                            "decide_at": (now + wait).isoformat()}
        return {"id": submission_id, "submitted_at": now.isoformat()}

    def submit(self, bundle: dict[str, Any], payer_name: str, now: datetime | None = None) -> dict[str, Any]:
        claim = bundle["entry"][0]["resource"]
        if claim.get("resourceType") != "Claim" or claim.get("use") != "preauthorization" or not claim.get("item"):
            raise ValueError("not a prior-authorization request bundle")
        submission_id = f"PA-{uuid.uuid4().hex[:10].upper()}"
        attested = [s for s in claim["supportingInfo"] if "Attested by" in s.get("valueString", "")]
        ahi = [(s, int(m.group(1))) for s in claim["supportingInfo"]
               if (m := re.search(r"Apnea-hypopnea index was (\d+)", s.get("valueString", "")))]
        decision: dict[str, Any]
        if attested and payer_name in self._pend_attested:
            decision = {"code": "A4", "display": "Pended",
                        "question": f"Send the clinical note that documents: {attested[0]['category']['text']}."}
        elif payer_name in self._strict and ahi and ahi[0][1] < 15:
            decision = {"code": "A3", "display": "Not certified",
                        "reason": {"criterion": ahi[0][0]["category"]["text"],
                                   "text": f"The apnea-hypopnea index of {ahi[0][1]} is below 15 events per hour, "
                                           "the threshold for coverage of a CPAP device."}}
        else:
            decision = {"code": "A1", "display": "Certified in total", "authorization_number": _authorization()}
        return self._record(submission_id, decision, claim, now)

    def respond(self, bundle: dict[str, Any], payer_name: str, submission_id: str,
                now: datetime | None = None) -> dict[str, Any]:
        """Documentation for a pended request. The decision is recorded against the original submission."""
        claim = bundle["entry"][0]["resource"]
        if claim.get("resourceType") != "Claim" or claim.get("use") != "preauthorization":
            raise ValueError("not a prior-authorization request bundle")
        related = [r["claim"]["identifier"]["value"] for r in claim.get("related", [])]
        if related != [submission_id]:
            raise ValueError("the reply must name the submission it answers")
        notes = [s for s in claim.get("supportingInfo", []) if (s.get("valueAttachment") or {}).get("data")]
        if notes:
            decision = {"code": "A1", "display": "Certified in total", "authorization_number": _authorization()}
        else:
            decision = {"code": "A4", "display": "Pended", "question": "Send the clinical note that was requested."}
        return self._record(submission_id, decision, claim, now)

    def appeal(self, bundle: dict[str, Any], payer_name: str, submission_id: str,
               now: datetime | None = None) -> dict[str, Any]:
        """Reconsideration of a denial. The mock reviewer overturns a denial when the appeal is attached."""
        claim = bundle["entry"][0]["resource"]
        related = [r["claim"]["identifier"]["value"] for r in claim.get("related", [])]
        if related != [submission_id]:
            raise ValueError("the appeal must name the decision it appeals")
        letters = [s for s in claim["supportingInfo"] if (s.get("valueAttachment") or {}).get("data")]
        decision = ({"code": "A1", "display": "Certified on appeal", "authorization_number": _authorization()}
                    if letters else
                    {"code": "A3", "display": "Not certified",
                     "reason": {"criterion": "", "text": "No appeal letter was attached."}})
        return self._record(submission_id, decision, claim, now)

    def status(self, submission_id: str, now: datetime | None = None) -> dict[str, Any]:
        """The decision once the reviewer has made it; until then, that the request is in review and by when."""
        s = self._submissions[submission_id]
        if (now or datetime.now(UTC)) < datetime.fromisoformat(s["decide_at"]):
            return {"id": s["id"], "submitted_at": s["submitted_at"], "system": s["system"], "decision": None,
                    "in_review": True, "expected_by": s["decide_at"], "priority": s["priority"]}
        return {k: v for k, v in s.items() if k != "decide_at"} | {"in_review": False}


def _authorization() -> str:
    return f"AUTH{uuid.uuid4().int % 10**8:08d}"
