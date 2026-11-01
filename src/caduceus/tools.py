"""Customer-system tools behind one gateway, grouped into the five MCP servers (ADR 3).

Before a tool runs, the gateway checks the caller's role and that the tool is on the allow-list for the workflow
step, so an instruction injected into a clinical note cannot make the agent act outside the current step. Every
call, allowed or refused, is audited with hashes of its input and output.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from caduceus.audit.log import sha256_of
from caduceus.auth import Principal, Role
from caduceus.criteria.dsl import CriteriaSet
from caduceus.fhir.record import (
    codings,
    condition_is_active,
    condition_onset,
    display,
    observation_date,
    procedure_period,
)
from caduceus.fhir.store import FhirStore, document_text
from caduceus.retrieval import where_to_look
from caduceus.storage import AuditStore


class ToolForbidden(Exception):
    """The caller's role may not use this tool."""


class ToolNotAllowed(Exception):
    """The tool is not on the allow-list for the case's current workflow step."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    server: str
    roles: frozenset[Role]
    fn: Callable[..., Any]
    description: str
    writes: bool = False


ALLOWLIST: dict[str, frozenset[str]] = {
    "enrichment": frozenset({"ehr.get_patient_summary", "ehr.list_active_conditions", "ehr.list_procedures",
                             "ehr.search_observations", "documents.list_packet", "documents.search_packet",
                             "policies.criteria_set_for", "payer.coverage_requirements"}),
    "drafting": frozenset({"documents.list_packet", "documents.get_page"}),
    "review": frozenset({"tickets.set_state", "documents.get_page", "documents.search_packet"}),
    "intake": frozenset({"documents.read_fax"}),
    "submission": frozenset({"payer.submit", "payer.respond", "payer.appeal"}),
    "follow_up": frozenset({"payer.status", "tickets.set_state"}),
}

READ = frozenset({Role.AGENT_RUNTIME, Role.PA_SPECIALIST, Role.ADMIN})


@dataclass
class ToolGateway:
    audit: AuditStore
    tools: dict[str, ToolSpec] = field(default_factory=dict)

    def register(self, spec: ToolSpec) -> None:
        self.tools[spec.name] = spec

    def call(self, principal: Principal, case_id: str, step: str, name: str, /, **kwargs: Any) -> Any:
        spec = self.tools.get(name)
        if spec is None:
            raise KeyError(name)
        base = {"tool": name, "step": step, "input_sha256": sha256_of(kwargs)}
        if principal.role not in spec.roles:
            self.audit.append(principal.id, principal.role, "tool.forbidden", case_id, base)
            raise ToolForbidden(f"{principal.role} may not call {name}")
        if name not in ALLOWLIST.get(step, frozenset()):
            self.audit.append(principal.id, principal.role, "tool.not_allowed", case_id, base)
            raise ToolNotAllowed(f"{name} is not allowed during {step}")
        result = spec.fn(**kwargs)
        self.audit.append(principal.id, principal.role, "tool.call", case_id,
                          base | {"output_sha256": sha256_of(result), "writes": spec.writes})
        return result

    def for_server(self, server: str) -> list[ToolSpec]:
        return [t for t in self.tools.values() if t.server == server]


def _ago(as_of: date, days: int | None) -> date | None:
    return as_of - timedelta(days=days) if days is not None else None


def build_gateway(audit: AuditStore, store: FhirStore, library: dict[str, CriteriaSet],
                  set_state: Callable[[str, str, str], None], submit: Callable[..., dict],
                  status: Callable[[str], dict], respond: Callable[..., dict], appeal: Callable[..., dict],
                  requirements: Callable[..., dict], read_fax: Callable[[str], dict]) -> ToolGateway:
    gw = ToolGateway(audit)

    # ehr: narrow, read-scoped views of the record. No generic FHIR search is exposed.
    def get_patient_summary(patient_id: str, case_id: str, as_of: str) -> dict:
        rec = store.record(patient_id, case_id)
        born = date.fromisoformat(rec.patient["birthDate"])
        when = date.fromisoformat(as_of)
        age = when.year - born.year - ((when.month, when.day) < (born.month, born.day))
        return {"age": age, "sex": rec.patient.get("gender"), "resources": len(rec.resources)}

    def list_active_conditions(patient_id: str, case_id: str, as_of: str) -> list[dict]:
        rec, when = store.record(patient_id, case_id), date.fromisoformat(as_of)
        return [{"id": r["id"], "display": display(r), "codes": codings(r),
                 "onset": (condition_onset(r) or when).isoformat()}
                for r in rec.of_type("Condition") if condition_is_active(r, when)]

    def list_procedures(patient_id: str, case_id: str, as_of: str, within_days: int = 730) -> list[dict]:
        rec, since = store.record(patient_id, case_id), _ago(date.fromisoformat(as_of), within_days)
        out = []
        for r in rec.of_type("Procedure"):
            start, end = procedure_period(r)
            if start and (since is None or (end or start) >= since):
                out.append({"id": r["id"], "display": display(r), "start": start.isoformat(),
                            "end": (end or start).isoformat()})
        return out

    def search_observations(patient_id: str, case_id: str, as_of: str, code: str) -> list[dict]:
        rec = store.record(patient_id, case_id)
        return [{"id": r["id"], "display": display(r), "date": str(observation_date(r))}
                for r in rec.of_type("Observation") if any(c == code for _, c in codings(r))]

    for fn, desc in [(get_patient_summary, "Age, sex and record size for the case's patient."),
                     (list_active_conditions, "Active diagnoses as of the case date."),
                     (list_procedures, "Procedures performed within a look-back window."),
                     (search_observations, "Observations with a given code.")]:
        gw.register(ToolSpec(f"ehr.{fn.__name__}", "ehr", READ, fn, desc))

    # documents: the referral packet pages for a case.
    def list_packet(packet: list[str]) -> list[dict]:
        out = []
        for i, doc_id in enumerate(packet, start=1):
            doc = store.document(doc_id) or {}
            out.append({"id": doc_id, "page": i, "title": doc.get("description", "Page"), "date": doc.get("date")})
        return out

    def get_page(doc_id: str) -> dict:
        doc = store.document(doc_id)
        if doc is None:
            raise KeyError(doc_id)
        return {"id": doc_id, "title": doc.get("description"), "date": doc.get("date"),
                "author": (doc.get("author") or [{}])[0].get("display"), "text": document_text(doc)}

    def search_packet(packet: list[str], criteria_set: str, criterion_id: str, k: int = 5) -> list[dict]:
        """The packet passages most likely to bear on one criterion, searched for with the criteria set's words."""
        criteria = library[criteria_set]
        criterion = next(c for c in criteria.criteria if c.id == criterion_id)
        pages = [document_text(store.document(d) or {}) for d in packet]
        return [{"doc_id": packet[h.passage.page - 1], "page": h.passage.page, "text": h.passage.text,
                 "start": h.passage.start, "end": h.passage.end}
                for h in where_to_look(pages, criteria, criterion, max(1, min(k, 10)))]

    gw.register(ToolSpec("documents.list_packet", "documents", READ, list_packet, "Pages of a referral packet."))
    gw.register(ToolSpec("documents.search_packet", "documents", READ, search_packet,
                         "Packet passages that bear on one criterion, found inside the boundary."))
    gw.register(ToolSpec("documents.read_fax", "documents", frozenset({Role.AGENT_RUNTIME}), read_fax,
                         "Read a faxed referral cover sheet with OCR inside the boundary."))
    gw.register(ToolSpec("documents.get_page", "documents", READ, get_page, "Text of one packet page."))

    # policies: which approved criteria set governs a service.
    def criteria_set_for(key: str) -> dict:
        s = library[key]
        return {"key": s.key, "title": s.title, "criteria": [{"id": c.id, "text": c.text} for c in s.criteria]}

    gw.register(ToolSpec("policies.criteria_set_for", "policies", READ, criteria_set_for,
                         "The approved criteria set for a service."))

    # tickets and payer: the only writes, and only for the agent runtime.
    gw.register(ToolSpec("tickets.set_state", "tickets", frozenset({Role.AGENT_RUNTIME}), set_state,
                         "Move a case to another queue state.", writes=True))
    gw.register(ToolSpec("payer.submit", "payer", frozenset({Role.AGENT_RUNTIME}), submit,
                         "Submit an approved request to the payer.", writes=True))
    gw.register(ToolSpec("payer.respond", "payer", frozenset({Role.AGENT_RUNTIME}), respond,
                         "Send the payer the documentation it asked for, once a person has approved it.",
                         writes=True))
    gw.register(ToolSpec("payer.appeal", "payer", frozenset({Role.AGENT_RUNTIME}), appeal,
                         "Send an appeal of a denial, once a person has approved it.", writes=True))
    gw.register(ToolSpec("payer.coverage_requirements", "payer", frozenset({Role.AGENT_RUNTIME}), requirements,
                         "Ask the payer whether prior authorization is required and which policy it applies."))
    gw.register(ToolSpec("payer.status", "payer", frozenset({Role.AGENT_RUNTIME}), status,
                         "Read the payer's decision on a submission."))
    return gw
