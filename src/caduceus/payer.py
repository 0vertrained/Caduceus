"""The prior-authorization request bundle and a mock payer.

The request is shaped after the HL7 Da Vinci Prior Authorization Support (PAS) guide: a Bundle whose first entry
is a Claim with `use = preauthorization`. It is a simplified subset (no full profile validation in this slice).
Decisions use the X12 review-action codes PAS carries: A1 certified, A3 not certified, A4 pended.

When a payer pends a request and asks for documentation, the reply is a second Claim related to the first, carrying
the clinician's signed note as an attachment.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from typing import Any

from caduceus.cases import Case

REVIEW_ACTION = "https://codesystem.x12.org/005010/306"
SERVICE_SYSTEMS = {"cpt": "http://www.ama-assn.org/go/cpt",
                   "hcpcs": "https://www.cms.gov/Medicare/Coding/HCPCSReleaseCodeSets"}


def build_request_bundle(case: Case) -> dict[str, Any]:
    claim_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"caduceus/claim/{case.id}"))
    results = case.effective_results()
    return {
        "resourceType": "Bundle", "type": "collection",
        "identifier": {"system": "http://mesaverde.example/pa-request", "value": case.id},
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "entry": [{"fullUrl": f"urn:uuid:{claim_id}", "resource": {
            "resourceType": "Claim", "id": claim_id, "status": "active", "use": "preauthorization",
            "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type",
                                 "code": "professional"}]},
            "patient": {"reference": f"Patient/{case.patient_id}"},
            "created": datetime.now(UTC).date().isoformat(),
            "insurer": {"display": case.payer["name"]},
            "provider": {"reference": f"Practitioner/{case.ordering_clinician['id']}",
                         "display": case.ordering_clinician["name"]},
            "priority": {"coding": [{"code": "normal"}]},
            "supportingInfo": [
                {"sequence": i, "category": {"text": r["text"]},
                 "valueString": r.get("explanation", "") + (f" Attested by {r['attested_by']}."
                                                            if r.get("attested_by") else "")}
                for i, r in enumerate(results, start=1)],
            "item": [{"sequence": 1, "productOrService": {"coding": [{
                "system": SERVICE_SYSTEMS[case.service["system"]], "code": case.service["code"],
                "display": case.service["display"]}]}}],
        }}],
    }


def build_reply_bundle(case: Case) -> dict[str, Any]:
    """The documentation a payer asked for, as an update to the original request."""
    note = (case.reply or {})["note"]
    original = (case.payer_response or {})["id"]
    claim_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"caduceus/claim/{case.id}/reply/{note['signed_at']}"))
    return {
        "resourceType": "Bundle", "type": "collection",
        "identifier": {"system": "http://mesaverde.example/pa-request", "value": case.id},
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "entry": [{"fullUrl": f"urn:uuid:{claim_id}", "resource": {
            "resourceType": "Claim", "id": claim_id, "status": "active", "use": "preauthorization",
            "type": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/claim-type",
                                 "code": "professional"}]},
            "patient": {"reference": f"Patient/{case.patient_id}"},
            "created": datetime.now(UTC).date().isoformat(),
            "insurer": {"display": case.payer["name"]},
            "provider": {"reference": f"Practitioner/{case.ordering_clinician['id']}",
                         "display": case.ordering_clinician["name"]},
            "priority": {"coding": [{"code": "normal"}]},
            "related": [{"claim": {"identifier": {"system": "http://mesaverde.example/payer-submission",
                                                  "value": original}},
                         "relationship": {"coding": [{
                             "system": "http://terminology.hl7.org/CodeSystem/related-claim-relationship",
                             "code": "prior"}]}}],
            "supportingInfo": [{
                "sequence": 1, "category": {"text": "Documentation the payer requested"},
                "valueAttachment": {"contentType": "text/plain; charset=utf-8",
                                    "data": base64.b64encode(note["text"].encode()).decode(),
                                    "title": f"Addendum by {note['author']}", "creation": note["signed_at"]}}],
            "item": [{"sequence": 1, "productOrService": {"coding": [{
                "system": SERVICE_SYSTEMS[case.service["system"]], "code": case.service["code"],
                "display": case.service["display"]}]}}],
        }}],
    }


class MockPayer:
    """Approves complete requests. Payers in `pend_attested` ask for the note behind any attested criterion, and
    approve once a reply carries a note."""

    def __init__(self, pend_attested: frozenset[str] = frozenset({"Humana Medicare Advantage"})):
        self._pend_attested = pend_attested
        self._submissions: dict[str, dict[str, Any]] = {}

    def submit(self, bundle: dict[str, Any], payer_name: str) -> dict[str, Any]:
        claim = bundle["entry"][0]["resource"]
        if claim.get("resourceType") != "Claim" or claim.get("use") != "preauthorization" or not claim.get("item"):
            raise ValueError("not a prior-authorization request bundle")
        submission_id = f"PA-{uuid.uuid4().hex[:10].upper()}"
        attested = [s for s in claim["supportingInfo"] if "Attested by" in s.get("valueString", "")]
        if attested and payer_name in self._pend_attested:
            decision = {"code": "A4", "display": "Pended",
                        "question": f"Send the clinical note that documents: {attested[0]['category']['text']}."}
        else:
            decision = {"code": "A1", "display": "Certified in total",
                        "authorization_number": f"AUTH{uuid.uuid4().int % 10**8:08d}"}
        self._submissions[submission_id] = {"id": submission_id, "submitted_at": datetime.now(UTC).isoformat(),
                                            "decision": decision, "system": REVIEW_ACTION}
        return {"id": submission_id, "submitted_at": self._submissions[submission_id]["submitted_at"]}

    def respond(self, bundle: dict[str, Any], payer_name: str, submission_id: str) -> dict[str, Any]:
        """Documentation for a pended request. The decision is recorded against the original submission."""
        claim = bundle["entry"][0]["resource"]
        if claim.get("resourceType") != "Claim" or claim.get("use") != "preauthorization":
            raise ValueError("not a prior-authorization request bundle")
        related = [r["claim"]["identifier"]["value"] for r in claim.get("related", [])]
        if related != [submission_id]:
            raise ValueError("the reply must name the submission it answers")
        notes = [s for s in claim.get("supportingInfo", []) if (s.get("valueAttachment") or {}).get("data")]
        if notes:
            decision = {"code": "A1", "display": "Certified in total",
                        "authorization_number": f"AUTH{uuid.uuid4().int % 10**8:08d}"}
        else:
            decision = {"code": "A4", "display": "Pended", "question": "Send the clinical note that was requested."}
        received_at = datetime.now(UTC).isoformat()
        self._submissions[submission_id] = {"id": submission_id, "submitted_at": received_at, "decision": decision,
                                            "system": REVIEW_ACTION}
        return {"id": submission_id, "submitted_at": received_at}

    def status(self, submission_id: str) -> dict[str, Any]:
        return self._submissions[submission_id]
