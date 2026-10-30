"""Drafting the request justification, and the chart summary a clinician sees when attesting.

The draft is written over de-identified text through the router's external door; tokens such as [DATE_2] come
back in the model's output and are filled in locally. With no model configured, or if the model is down, a
deterministic template writes the draft instead, so the workflow never depends on a model being available.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from caduceus.cases import Case
from caduceus.deid.safe_harbor import PatientContext, SafeHarborDeidentifier, reidentify
from caduceus.router import InVpcUnavailable, Router, UnstampedPayload
from caduceus.router.router import ProviderUnavailable

DRAFT_SYSTEM = (
    "You write the clinical justification paragraph of a prior-authorization request for a medical group. "
    "Use only the facts provided. Do not add diagnoses, dates, test results or opinions that are not in the facts. "
    "Keep every bracketed token such as [NAME_1] or [DATE_2] exactly as written. If a criterion is awaiting "
    "clinician attestation, say so plainly. Write 90 to 140 words of formal, plain English, one paragraph, no "
    "headings, no salutation."
)

SUMMARY_SYSTEM = (
    "Summarize, for the ordering clinician, what this patient's chart shows about the listed criteria, in at most "
    "two sentences. State plainly what the chart does not say. Use only the facts provided."
)


def patient_context(patient: dict) -> PatientContext:
    names, phones, emails = [], [], []
    for n in patient.get("name", []):
        full = " ".join(n.get("given", []) + [n.get("family", "")]).strip()
        if full:
            names.append(full)
    for t in patient.get("telecom", []):
        (phones if t.get("system") == "phone" else emails).append(t.get("value", ""))
    addr = (patient.get("address") or [{}])[0]
    return PatientContext(
        names=names,
        birth_date=date.fromisoformat(patient["birthDate"]) if patient.get("birthDate") else None,
        address_lines=addr.get("line", []),
        city=addr.get("city"),
        postal_code=addr.get("postalCode"),
        phones=[p for p in phones if p],
        emails=[e for e in emails if e],
        record_numbers=[i.get("value", "") for i in patient.get("identifier", []) if len(i.get("value", "")) > 4],
    )


def mid_sentence(text: str) -> str:
    """Lower the first letter for use mid-sentence, leaving acronyms and names like MRI, CPAP or X-ray alone."""
    if len(text) > 1 and (text[1].isupper() or text[1] == "-"):
        return text
    return text[:1].lower() + text[1:]


def patient_name(patient: dict) -> str:
    n = next((n for n in patient.get("name", []) if n.get("use") in (None, "official")), patient["name"][0])
    return " ".join(n.get("given", [])[:1] + [n.get("family", "")]).strip()


def age_on(patient: dict, as_of: date) -> int:
    born = date.fromisoformat(patient["birthDate"])
    return as_of.year - born.year - ((as_of.month, as_of.day) < (born.month, born.day))


def _facts(case: Case, patient: dict) -> str:
    age = age_on(patient, date.fromisoformat(case.as_of))
    lines = [f"Patient: {patient_name(patient)}, {age}-year-old {patient.get('gender', 'patient')}.",
             f"Requested service: {case.service['display']} ({case.service['system'].upper()} "
             f"{case.service['code']}).", "Criteria:"]
    for r in case.effective_results():
        state = {"met": "met", "unmet": "not met", "unknown": "awaiting clinician attestation"}[r["status"]]
        lines.append(f"- {r['text']}: {state}. {r['explanation']}")
        for e in r.get("evidence", [])[:2]:
            if e.get("source_sentence"):
                lines.append(f"  Source: \"{e['source_sentence']}\"")
    return "\n".join(lines)


def template_draft(case: Case, patient: dict) -> str:
    age = age_on(patient, date.fromisoformat(case.as_of))
    sex = {"male": "man", "female": "woman"}.get(patient.get("gender", ""), "patient")
    parts = [f"Request for {mid_sentence(case.service['display'])} ({case.service['system'].upper()} "
             f"{case.service['code']}) for a {age}-year-old {sex}."]
    for r in case.effective_results():
        if r["status"] == "met":
            parts.append(f"{r['text']}: {r['explanation']}")
    pending = [mid_sentence(r["text"]) for r in case.effective_results() if r["status"] == "unknown"]
    if pending:
        parts.append("Awaiting clinician attestation: " + "; ".join(pending) + ".")
    return " ".join(parts)


class Drafter:
    def __init__(self, router: Router | None, deid: SafeHarborDeidentifier):
        self._router = router
        self._deid = deid

    def draft(self, case: Case, patient: dict) -> dict[str, Any]:
        template = template_draft(case, patient)
        if self._router is None:
            return {"text": template, "route": "template", "model": None}
        ctx = patient_context(patient)
        result = self._deid.deidentify(_facts(case, patient), ctx)
        stamped = self._deid.stamp(result)
        try:
            out = self._router.complete_deid("draft_request", DRAFT_SYSTEM, [stamped], case.id, ctx)
        except (InVpcUnavailable, ProviderUnavailable, UnstampedPayload) as e:
            return {"text": template, "route": "template", "model": None, "fallback_reason": type(e).__name__}
        reply = out.completion.text.strip()
        text = reidentify(reply, result.token_map)
        # What left Caduceus, for the reviewer: it is de-identified by construction, so it may be kept and shown.
        sent = {"text": stamped.text, "policy": stamped.stamp.policy, "text_sha256": stamped.stamp.text_sha256,
                "vendor": out.completion.vendor, "cached": out.completion.cached,
                "input_tokens": out.completion.input_tokens, "output_tokens": out.completion.output_tokens,
                "tokens_filled_in": sum(reply.count(token) for token in result.token_map)}
        return {"text": text, "route": out.route, "model": out.completion.model,
                "deid_removed": dict(result.removed), "sent": sent}

    def chart_summary(self, case: Case, patient: dict, criterion_ids: list[str]) -> str:
        results = [r for r in case.effective_results() if r["criterion_id"] in criterion_ids]
        known = [r for r in case.effective_results() if r["status"] == "met"][:2]
        sentences = [r["explanation"] for r in known]
        sentences += [f"The chart does not say whether this was done: {mid_sentence(r['text'])}." for r in results]
        fallback = " ".join(sentences)
        if self._router is None:
            return fallback
        try:
            out = self._router.complete_phi("attestation_summary", SUMMARY_SYSTEM, _facts(case, patient), case.id)
        except (InVpcUnavailable, ProviderUnavailable):
            return fallback
        return out.completion.text.strip()
