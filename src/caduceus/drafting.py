"""Drafting the request justification, and the chart summary a clinician sees when attesting.

The draft is written over de-identified text through the router's external door; tokens such as [DATE_2] come
back in the model's output and are filled in locally. With no model configured, or if the model is down, a
deterministic template writes the draft instead, so the workflow never depends on a model being available.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from caduceus.cases import Case
from caduceus.deid.safe_harbor import PatientContext, SafeHarborDeidentifier, reidentify
from caduceus.grounding import unsupported
from caduceus.router import InVpcUnavailable, Router, UnstampedPayload
from caduceus.router.router import ProviderUnavailable

DRAFT_SYSTEM = (
    "You write the clinical justification paragraph of a prior-authorization request for a medical group. "
    "Use only the facts provided. Do not add diagnoses, dates, test results or opinions that are not in the facts. "
    "Keep every bracketed token such as [NAME_1] or [DATE_2] exactly as written. If a criterion is awaiting "
    "clinician attestation, say so plainly. Write 90 to 140 words of formal, plain English, one paragraph, no "
    "headings, no salutation."
)

APPEAL_SYSTEM = (
    "You write a request for reconsideration of a prior-authorization denial for a medical group. Use only the facts "
    "provided. Quote the payer's stated reason and the coverage policy sentence exactly, then show how the documented "
    "facts meet the policy. Do not add diagnoses, dates or results that are not in the facts. Keep every bracketed "
    "token such as [NAME_1] or [DATE_2] exactly as written. Write 120 to 180 words of formal, plain English, no "
    "headings, no salutation, no signature."
)
SUMMARY_TOKENS = 400  # a short sentence per documented criterion; on a CPU each token takes about 0.1 s
SUMMARY_SYSTEM = (
    "For each criterion below, write one sentence for the ordering clinician saying what this patient's chart "
    "documents for it. Use only the facts under that criterion, and keep their numbers, dates and qualifiers as "
    "written. Write about the criterion only: not the patient's age or sex, and nothing the facts do not say. "
    "Answer with a JSON object that has one key per criterion id."
)


def patient_context(patient: dict) -> PatientContext:
    names: list[str] = []
    phones: list[str] = []
    emails: list[str] = []
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


def _patient_line(case: Case, patient: dict) -> str:
    age = age_on(patient, date.fromisoformat(case.as_of))
    return f"Patient: {patient_name(patient)}, {age}-year-old {patient.get('gender', 'patient')}."


def _sources(result: dict) -> list[str]:
    return [e["source_sentence"] for e in result.get("evidence", [])[:2] if e.get("source_sentence")]


def _facts(case: Case, patient: dict) -> str:
    """The case's facts for a draft: the patient, the service, and every criterion with its state."""
    lines = [_patient_line(case, patient),
             f"Requested service: {case.service['display']} ({case.service['system'].upper()} "
             f"{case.service['code']}).", "Criteria:"]
    for r in case.effective_results():
        state = {"met": "met", "unmet": "not met", "unknown": "awaiting clinician attestation"}[r["status"]]
        lines.append(f"- {r['text']}: {state}. {r['explanation']}")
        lines += [f'  Source: "{s}"' for s in _sources(r)]
    return "\n".join(lines)


def criterion_facts(result: dict) -> str:
    """A documented criterion's facts: everything a summary sentence about it may say."""
    return "\n".join([result["text"], result["explanation"], *(f'Source: "{s}"' for s in _sources(result))])


def engine_sentence(result: dict) -> str:
    """A criterion's result in the record's own words."""
    return f"{result['text']}: {mid_sentence(result['explanation'])}"


def unanswered(case: Case, criterion_ids: list[str]) -> list[str]:
    """A sentence for each criterion the clinician is asked about, which the chart does not answer."""
    return [f"The chart does not say whether this was done: {mid_sentence(r['text'])}."
            for r in case.effective_results() if r["criterion_id"] in criterion_ids]


def _summary_request(case: Case, patient: dict, documented: list[dict]) -> tuple[str, dict[str, Any]]:
    """The facts the model summarizes, one block per criterion keyed by its id, and the JSON shape of its answer."""
    facts = "\n\n".join([_patient_line(case, patient),
                         *(f"[{r['criterion_id']}] {criterion_facts(r)}" for r in documented)])
    ids = [r["criterion_id"] for r in documented]
    schema = {"type": "object", "properties": {i: {"type": "string", "maxLength": 300} for i in ids},
              "required": ids, "additionalProperties": False}
    return facts, schema


def _written(reply: str) -> dict[str, str] | None:
    """The model's sentence for each criterion id, or None if the reply is not the JSON object asked for."""
    try:
        data = json.loads(reply)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    return {k: " ".join(v.split()) for k, v in data.items() if isinstance(v, str) and v.strip()}


def _as_sentence(text: str) -> str:
    return text if text.endswith((".", "!", "?")) else f"{text}."


# A draft may not claim every criterion is met while one is not, or still waits on the clinician.
CLAIMS_ALL_MET = re.compile(
    r"\b(?:all (?:of )?(?:the )?(?:listed |documented |coverage |required )?criteria (?:are|have been|were) "
    r"(?:met|satisfied)|meets (?:all )?(?:of )?(?:the )?(?:listed |documented |coverage |required )?criteria)\b", re.I)


def contradicts(facts: str, reply: str) -> str | None:
    """Why a reply misstates the facts' criteria, or None. Deterministic, so it can gate every draft."""
    open_items = "awaiting clinician attestation" in facts or ": not met." in facts
    if open_items and CLAIMS_ALL_MET.search(re.sub(r"\s+", " ", reply)):  # a non-breaking space must not hide it
        return "the draft said every criterion is met, but the facts list one that is not"
    return None


def _denied(case: Case) -> tuple[dict[str, Any], dict[str, Any] | None]:
    reason = (case.payer_response or {}).get("decision", {}).get("reason") or {}
    result = next((r for r in case.effective_results() if r["text"] == reason.get("criterion")), None)
    return reason, result


def _appeal_facts(case: Case, patient: dict) -> str:
    reason, r = _denied(case)
    lines = [_facts(case, patient), "", f"Payer: {case.payer['name']}.",
             f"Payer's reason for denial: {reason.get('text')}"]
    if r:
        lines += [f"Criterion the payer cited: {r['text']}.", f"Coverage policy sentence: \"{r['policy_sentence']}\"",
                  f"What the record shows: {r['explanation']}"]
    return "\n".join(lines)


def template_appeal(case: Case, patient: dict) -> str:
    age = age_on(patient, date.fromisoformat(case.as_of))
    sex = {"male": "man", "female": "woman"}.get(patient.get("gender", ""), "patient")
    reason, r = _denied(case)
    parts = [f"We ask {case.payer['name']} to reconsider its denial of {mid_sentence(case.service['display'])} "
             f"({case.service['system'].upper()} {case.service['code']}) for a {age}-year-old {sex}.",
             f"The denial states: \"{reason.get('text', '').strip()}\""]
    if r:
        parts += [f"The coverage policy for this service reads: \"{r['policy_sentence']}\"",
                  f"The record meets it as written. {r['explanation']}"]
        source = next((e["source_sentence"] for e in r.get("evidence", []) if e.get("source_sentence")), None)
        if source:
            parts.append(f"The chart states: \"{source}\"")
    parts.append("We ask that the denial be reversed and the request approved.")
    return " ".join(parts)


def template_draft(case: Case, patient: dict) -> str:
    age = age_on(patient, date.fromisoformat(case.as_of))
    sex = {"male": "man", "female": "woman"}.get(patient.get("gender", ""), "patient")
    parts = [f"Request for {mid_sentence(case.service['display'])} ({case.service['system'].upper()} "
             f"{case.service['code']}) for a {age}-year-old {sex}."]
    parts += [f"{r['text']}: {r['explanation']}" for r in case.effective_results() if r["status"] == "met"]
    pending = [mid_sentence(r["text"]) for r in case.effective_results() if r["status"] == "unknown"]
    if pending:
        parts.append("Awaiting clinician attestation: " + "; ".join(pending) + ".")
    return " ".join(parts)


class Drafter:
    def __init__(self, router: Router | None, deid: SafeHarborDeidentifier):
        self._router = router
        self._deid = deid

    def draft(self, case: Case, patient: dict) -> dict[str, Any]:
        return self._write(case, patient, "draft_request", DRAFT_SYSTEM, _facts(case, patient),
                           template_draft(case, patient))

    def appeal(self, case: Case, patient: dict) -> dict[str, Any]:
        """The request for reconsideration after a denial, written the same way as the request itself."""
        return self._write(case, patient, "draft_appeal", APPEAL_SYSTEM, _appeal_facts(case, patient),
                           template_appeal(case, patient))

    def _write(self, case: Case, patient: dict, task: str, system: str, facts: str, template: str) -> dict[str, Any]:
        if self._router is None:
            return {"text": template, "route": "template", "model": None}
        ctx = patient_context(patient)
        result = self._deid.deidentify(facts, ctx)
        stamped = self._deid.stamp(result)
        try:
            out = self._router.complete_deid(task, system, [stamped], case.id, ctx)
        except (InVpcUnavailable, ProviderUnavailable, UnstampedPayload) as e:
            return {"text": template, "route": "template", "model": None,
                    "fallback_reason": f"{type(e).__name__}: {e}"[:200]}
        reply = out.completion.text.strip()
        wrong = contradicts(facts, reply) if task == "draft_request" else None
        if wrong:
            return {"text": template, "route": "template", "model": None, "fallback_reason": f"Rejected: {wrong}"}
        text = reidentify(reply, result.token_map)
        # What left Caduceus, for the reviewer: it is de-identified by construction, so it may be kept and shown.
        sent = {"text": stamped.text, "policy": stamped.stamp.policy, "text_sha256": stamped.stamp.text_sha256,
                "vendor": out.completion.vendor, "cached": out.completion.cached,
                "input_tokens": out.completion.input_tokens, "output_tokens": out.completion.output_tokens,
                "tokens_filled_in": sum(reply.count(token) for token in result.token_map)}
        return {"text": text, "route": out.route, "model": out.completion.model,
                "deid_removed": dict(result.removed), "sent": sent}

    @property
    def phi_route(self) -> bool:
        """True when a model inside the network can do work that needs the patient's details."""
        return self._router is not None and self._router.in_vpc_enabled

    @staticmethod
    def template_summary(case: Case, criterion_ids: list[str]) -> dict[str, Any]:
        """What the chart shows, in the record's own words: at once, and always available."""
        sentences = [engine_sentence(r) for r in case.effective_results() if r["status"] == "met"]
        return {"text": " ".join(sentences + unanswered(case, criterion_ids)), "route": "template", "model": None}

    def chart_summary(self, case: Case, patient: dict, criterion_ids: list[str],
                      keep_sentences: bool = False) -> dict[str, Any]:
        """The in-network model's summary, which may take tens of seconds on a CPU.

        The model writes a sentence for each criterion the chart documents, from that criterion's facts alone. A
        sentence that says more than its facts is replaced with the record's own words, and what the clinician is
        asked about is always in the engine's words. The template's summary stands in when there is no such model,
        it is down, or none of its sentences hold. `keep_sentences` adds what the model wrote for each criterion,
        for the summary eval.
        """
        documented = [r for r in case.effective_results() if r["status"] == "met"]
        if self._router is None or not self._router.in_vpc_enabled or not documented:
            return self.template_summary(case, criterion_ids)
        facts, schema = _summary_request(case, patient, documented)
        try:
            out = self._router.complete_phi("attestation_summary", SUMMARY_SYSTEM, facts, case.id,
                                            max_tokens=SUMMARY_TOKENS, schema=schema)
        except (InVpcUnavailable, ProviderUnavailable):
            return self.template_summary(case, criterion_ids) | {"fallback_reason": "in_vpc_unavailable"}
        written = _written(out.completion.text)
        if written is None:
            return self.template_summary(case, criterion_ids) | {"fallback_reason": "unreadable"} | (
                {"reply": out.completion.text} if keep_sentences else {})
        context = _patient_line(case, patient)
        sentences, checked = [], []
        for r in documented:
            sentence = written.get(r["criterion_id"], "")
            why = unsupported(sentence, f"{context}\n{criterion_facts(r)}") if sentence else "missing"
            sentences.append(engine_sentence(r) if why else _as_sentence(sentence))
            checked.append({"criterion_id": r["criterion_id"], "written": sentence, "reason": why})
        replaced = [{"criterion_id": c["criterion_id"], "reason": c["reason"]} for c in checked if c["reason"]]
        extra = {"sentences": checked} if keep_sentences else {}
        if len(replaced) == len(documented):
            return self.template_summary(case, criterion_ids) | {"fallback_reason": "unsupported",
                                                                 "replaced": replaced} | extra
        return {"text": " ".join(sentences + unanswered(case, criterion_ids)), "route": out.route,
                "model": out.completion.model, "replaced": replaced} | extra
