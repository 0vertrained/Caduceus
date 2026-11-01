"""How well the in-network model's chart summaries hold up against the record.

    python -m caduceus.evals.summaries --report evals/reports/summaries.json

Needs the model: CADUCEUS_IN_VPC_URL and CADUCEUS_IN_VPC_MODEL. Every case with a documented criterion is
summarized as a clinician would see it: the dataset's cases with the showcase's, and the hard sets' dev split by
default. For each sentence the report gives what the model wrote and whether the check kept or replaced it, and
why, so the check can be read as well as the model. The held-out split is left for one run once the check is
settled (`--sets dataset,dev,heldout`).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import Counter
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

from caduceus.app_factory import Services, Settings, build_services
from caduceus.cases import Case
from caduceus.criteria.engine import Engine
from caduceus.criteria.loader import load_library, load_lock
from caduceus.drafting import Drafter
from caduceus.fhir.store import LocalBundleStore
from caduceus.seed import seed

ROOT = Path(__file__).resolve().parents[3]
DATA = ROOT / "data"
EVAL_KEY = "summary-eval-only-key-at-least-32-bytes"


def services(url: str, model: str) -> Services:
    """The dataset's cases and the showcase's, prepared as the demo prepares them, with the model to evaluate."""
    svc = build_services(Settings(data_dir=DATA, db_path=":memory:", auth_mode="dev", deid_key=EVAL_KEY,
                                  showcase="1", external_provider="stub", in_vpc_url=url, in_vpc_model=model,
                                  in_vpc_drafts="0"))
    svc.workflow.later = lambda *args: None  # summaries are written here, one at a time, not in the background
    seed(svc)
    return svc


def dataset_cases(svc: Services) -> Iterator[tuple[str, Case, dict]]:
    for case in sorted(svc.cases.all(), key=lambda c: c.id):
        yield "dataset", case, svc.store.record(case.patient_id, case.id).patient


def hard_cases(split: str) -> Iterator[tuple[str, Case, dict]]:
    """A hard set's cases, checked by the engine against the criteria they name."""
    data, policies = DATA / "hard" / split, DATA / "policies"
    library, engine, store = load_library(policies), Engine(load_lock(policies)), LocalBundleStore(data)
    for c in json.loads((data / "cases" / "cases.json").read_text()):
        record = store.record(c["patient_id"], c["id"])
        found = engine.evaluate(library[c["criteria_set"]], record, date.fromisoformat(c["as_of"]))
        case = Case.model_construct(id=c["id"], as_of=c["as_of"], determination=found.to_dict(), attestations=[])
        yield split, case, record.patient


def summarize(drafter: Drafter, cases: Iterator[tuple[str, Case, dict]]) -> list[dict[str, Any]]:
    rows = []
    for group, case, patient in cases:
        if not any(r["status"] == "met" for r in case.effective_results()):
            continue
        asked = [r["criterion_id"] for r in case.effective_results() if r["status"] == "unknown"]
        start = time.monotonic()
        summary = drafter.chart_summary(case, patient, asked, keep_sentences=True)
        rows.append({"set": group, "case": case.id, "seconds": round(time.monotonic() - start, 1),
                     "route": summary["route"], "fallback_reason": summary.get("fallback_reason"),
                     "sentences": summary.get("sentences", []), "reply": summary.get("reply"),
                     "text": summary["text"]})
        print(f"{group:8} {case.id:24} {rows[-1]['seconds']:6.1f}s  {summary['route']:8} "
              f"{summary.get('fallback_reason') or ''}", flush=True)
        for s in rows[-1]["sentences"]:
            verdict = f"replaced ({s['reason']}: {s['found'][s['reason']]})" if s["reason"] else "kept"
            print(f"    {s['criterion_id']}: {verdict}\n      {s['written']}", flush=True)
    return rows


def totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sentences = [s for r in rows for s in r["sentences"]]
    replaced = Counter(s["reason"] for s in sentences if s["reason"])
    kept = len(sentences) - sum(replaced.values())
    return {"summaries": len(rows), "sentences": len(sentences), "kept": kept,
            "kept_share": round(kept / len(sentences), 3) if sentences else None,
            "replaced": dict(replaced.most_common()),
            "fallbacks": dict(Counter(r["fallback_reason"] for r in rows if r["fallback_reason"]).most_common()),
            "seconds_per_summary": round(sum(r["seconds"] for r in rows) / len(rows), 1) if rows else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--sets", default="dataset,dev", help="dataset, dev and/or heldout, comma-separated")
    parser.add_argument("--report", type=Path, help="write every summary and the totals to this JSON file")
    args = parser.parse_args()
    url, model = os.environ.get("CADUCEUS_IN_VPC_URL"), os.environ.get("CADUCEUS_IN_VPC_MODEL")
    if not url or not model:
        raise SystemExit("Set CADUCEUS_IN_VPC_URL and CADUCEUS_IN_VPC_MODEL to the model to evaluate.")
    svc = services(url, model)
    rows: list[dict[str, Any]] = []
    for name in args.sets.split(","):
        rows += summarize(svc.workflow.drafter, dataset_cases(svc) if name == "dataset" else hard_cases(name))
    result = {"model": model, "sets": args.sets, "totals": totals(rows), "summaries": rows}
    print(json.dumps(result["totals"], indent=2))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
