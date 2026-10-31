"""The coverage policies as a reviewer reads them: for each service, the version it runs under now, where its words
come from, who approved it, whether it is unchanged since, and the versions it replaced."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import BaseModel

from caduceus.criteria.dsl import Code, CriteriaSet
from caduceus.criteria.loader import active
from caduceus.words import long_date

SYSTEMS = {"icd10": "ICD-10", "cpt": "CPT", "hcpcs": "HCPCS", "snomed": "SNOMED CT", "loinc": "LOINC",
           "local": "Mesa Verde"}


def codes(rule: Any) -> list[Code]:
    """Every code a rule looks for, nested rules included, each once, in the order the rule gives them."""
    found: list[Code] = []
    if isinstance(rule, BaseModel):
        for name in type(rule).model_fields:
            value = getattr(rule, name)
            items = value if isinstance(value, list) else [value]
            for item in items:
                if isinstance(item, Code):
                    found.append(item)
                elif isinstance(item, BaseModel):
                    found.extend(codes(item))
    seen: set[tuple[str, str]] = set()
    return [c for c in found if (c.system, c.code) not in seen and not seen.add((c.system, c.code))]


def describe(s: CriteriaSet, lock: dict[str, str], with_criteria: bool) -> dict[str, Any]:
    digest = s.content_sha256()
    out: dict[str, Any] = {
        "key": s.key, "version": s.version, "title": s.title, "status": s.status,
        "approved_by": s.approved_by, "approved_at": long_date(s.approved_at) if s.approved_at else None,
        "source": {"kind": s.source.kind, "title": s.source.title, "document": s.source.document,
                   "url": s.source.url, "note": s.source.note,
                   "retrieved": long_date(s.source.retrieved) if s.source.retrieved else None},
        "sha256": digest, "intact": lock.get(s.key) == digest, "supersedes": s.supersedes,
    }
    if with_criteria:
        out["criteria"] = [{"id": c.id, "text": c.text, "kind": c.kind, "policy_sentence": c.policy_sentence,
                            "codes": [{"system": SYSTEMS.get(k.system, k.system), "code": k.code,
                                       "display": k.display} for k in codes(c.rule)]}
                           for c in s.criteria]
    return out


def policies(library: dict[str, CriteriaSet], lock: dict[str, str]) -> list[dict[str, Any]]:
    """One entry per service: the set it runs under now, with its criteria, and every other version of it."""
    current = active(library, lock)
    versions: dict[str, list[CriteriaSet]] = defaultdict(list)
    for s in library.values():
        for c in s.service_codes:
            versions[c.code].append(s)
    out = []
    for code, running in sorted(current.items(), key=lambda kv: kv[1].title):
        earlier = sorted((s for s in versions[code] if s.key != running.key), key=lambda s: s.version, reverse=True)
        out.append({"code": code, "running": describe(running, lock, with_criteria=True),
                    "earlier": [describe(s, lock, with_criteria=False) for s in earlier]})
    return out
