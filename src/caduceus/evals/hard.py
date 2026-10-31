"""The hard sets: does the engine hold up on messy records, and can retrieval find the evidence in long packets?

    python -m caduceus.evals.hard --split dev
    python -m caduceus.evals.hard --split heldout --report evals/hard/heldout-first-run.json

Engine: agreement with the labels, and every disagreement sorted by what it would cost:
- unsafe: the engine says met when the answer is unmet or unknown, so a request could go out unsupported;
- wrong block: the engine says unmet when the answer is met or unknown, so a person must overturn it;
- needless question: the engine says unknown when the answer is met, so a clinician is asked for nothing;
- missed block: the engine says unknown when the answer is unmet, so a clinician is asked instead of the case
  being stopped.

Retrieval: for every criterion whose packet holds sentences about it, the rank of the first passage that overlaps
one of them. Recall at 1 and 5 and mean reciprocal rank (over the top 10), for plain BM25 (no clinical
normalization), the word channel, and the product's default (words and trigrams, fused). Text-only evidence, where
the structured record holds nothing, is reported on its own: it is what a clinician is asked to attest.

The held-out split is run once, and that first run is committed as it came out. Fixes made after it are measured
on the dev split, and a held-out run after a fix is no longer a held-out result.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path

from caduceus.criteria.engine import Engine
from caduceus.criteria.loader import load_library, load_lock
from caduceus.fhir.store import LocalBundleStore, document_text
from caduceus.retrieval import STOP, _bm25, _ranks, criterion_query, passages, search, where_to_look

ROOT = Path(__file__).resolve().parents[3]
COSTS = {("unmet", "met"): "unsafe", ("unknown", "met"): "unsafe", ("met", "unmet"): "wrong block",
         ("unknown", "unmet"): "wrong block", ("met", "unknown"): "needless question",
         ("unmet", "unknown"): "missed block"}
TEXT_ONLY = re.compile(r"notes_only|finding_not_charted")


def engine_suite(data: Path, policies: Path) -> dict:
    library, engine, store = load_library(policies), Engine(load_lock(policies)), LocalBundleStore(data)
    cases = json.loads((data / "cases" / "cases.json").read_text())
    confusion: Counter[tuple[str, str]] = Counter()
    errors = []
    for c in cases:
        det = engine.evaluate(library[c["criteria_set"]], store.record(c["patient_id"], c["id"]),
                              date.fromisoformat(c["as_of"]))
        for r in det.results:
            want, got = c["expected"][r.criterion_id], r.status.value
            confusion[(want, got)] += 1
            if want != got:
                errors.append({"case": c["id"], "criterion": r.criterion_id, "state": c["states"][r.criterion_id],
                               "expected": want, "engine": got, "cost": COSTS[(want, got)]})
    total = sum(confusion.values())
    return {
        "cases": len(cases), "criteria": total,
        "agreement": round(sum(n for (w, g), n in confusion.items() if w == g) / total, 4),
        "errors_by_cost": dict(Counter(e["cost"] for e in errors).most_common()),
        "errors_by_state": dict(Counter(f"{e['criterion']}: {e['state']}" for e in errors).most_common()),
        "confusion": {f"{w}->{g}": n for (w, g), n in sorted(confusion.items())},
        "errors": errors,
    }


def _plain(pages: list[str], query: str, k: int) -> list:
    """Baseline: BM25 on lower-cased words, with no clinical normalization and no trigrams."""
    ps = passages(pages)
    toks = [[w for w in re.findall(r"[a-z0-9]+", p.text.lower()) if w not in STOP] for p in ps]
    q = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if w not in STOP]
    ranks = _ranks(_bm25(toks, q))
    order = sorted((i for i, r in enumerate(ranks) if r), key=lambda i: ranks[i])
    return [ps[i] for i in order[:k]]


VARIANTS = {
    "plain_bm25": lambda pages, q, k: _plain(pages, q, k),
    "words": lambda pages, q, k: [h.passage for h in search(pages, q, k, channels=("words",))],
    "words_and_trigrams": lambda pages, q, k: [h.passage for h in search(pages, q, k)],
}


def retrieval_suite(data: Path, policies: Path, k: int = 10) -> dict:
    library, store = load_library(policies), LocalBundleStore(data)
    cases = json.loads((data / "cases" / "cases.json").read_text())
    out: dict[str, dict] = {}
    misses = []
    for name, run in VARIANTS.items():
        ranks, text_only = [], []
        for c in cases:
            pages = [document_text(store.document(d) or {}) for d in c["packet"]]
            criteria = {cr.id: cr for cr in library[c["criteria_set"]].criteria}
            for crit, gold in c["evidence"].items():
                found = run(pages, criterion_query(criteria[crit]), k)
                rank = next((i for i, p in enumerate(found, start=1)
                             if any(p.page == g["page"] and p.start < g["end"] and g["start"] < p.end
                                    for g in gold)), None)
                ranks.append(rank)
                if TEXT_ONLY.search(c["states"][crit]):
                    text_only.append(rank)
                if name == "words_and_trigrams" and (rank is None or rank > 5):
                    misses.append({"case": c["id"], "criterion": crit, "state": c["states"][crit], "rank": rank,
                                   "gold": [" ".join(g["text"].split()) for g in gold],
                                   "top": [" ".join(p.text.split())[:120] for p in found[:3]]})
        out[name] = {"queries": len(ranks), "recall_at_1": _recall(ranks, 1), "recall_at_5": _recall(ranks, 5),
                     "mrr": round(sum(1 / r for r in ranks if r) / len(ranks), 3),
                     "text_only": {"queries": len(text_only), "recall_at_5": _recall(text_only, 5)}}
    return {"variants": out, "misses_at_5": misses}


def leads_suite(data: Path, policies: Path, k: int = 3) -> dict:
    """What "where to look" shows, exactly as the product does (the best 3 that clear the relevance floor). For a
    criterion whose packet holds sentences about it: whether a shown passage overlaps one. For a criterion whose packet
    holds none: how many passages are shown anyway, each one a false lead a person reads for nothing."""
    library, store = load_library(policies), LocalBundleStore(data)
    cases = json.loads((data / "cases" / "cases.json").read_text())
    found, shown_without = [], []
    for c in cases:
        pages = [document_text(store.document(d) or {}) for d in c["packet"]]
        cset = library[c["criteria_set"]]
        for crit in cset.criteria:
            hits = where_to_look(pages, cset, crit, k)
            gold = c["evidence"].get(crit.id)
            if gold:
                found.append(any(h.passage.page == g["page"] and h.passage.start < g["end"]
                                 and g["start"] < h.passage.end for h in hits for g in gold))
            else:
                shown_without.append(len(hits))
    return {"k": k, "with_evidence": len(found), "recall": round(sum(found) / len(found), 3) if found else 0.0,
            "without_evidence": len(shown_without),
            "false_leads_per_criterion": round(sum(shown_without) / len(shown_without), 2) if shown_without else 0.0,
            "none_shown": round(sum(1 for n in shown_without if n == 0) / len(shown_without), 3) if shown_without
            else 0.0}


def _recall(ranks: list[int | None], k: int) -> float:
    return round(sum(1 for r in ranks if r and r <= k) / len(ranks), 3) if ranks else 0.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "heldout"], default="dev")
    ap.add_argument("--report", type=Path)
    args = ap.parse_args()
    data, policies = ROOT / "data" / "hard" / args.split, ROOT / "data" / "policies"
    report = {"split": args.split, "engine": engine_suite(data, policies),
              "retrieval": retrieval_suite(data, policies), "where_to_look": leads_suite(data, policies)}
    e, r = report["engine"], report["retrieval"]["variants"]
    print(f"{args.split}: engine agreement {e['agreement']} on {e['criteria']} criteria; errors {e['errors_by_cost']}")
    for name, v in r.items():
        print(f"  retrieval {name:20} recall@1 {v['recall_at_1']:.3f}  recall@5 {v['recall_at_5']:.3f}  "
              f"MRR {v['mrr']:.3f}  text-only recall@5 {v['text_only']['recall_at_5']:.3f} "
              f"({v['text_only']['queries']})")
    w = report["where_to_look"]
    print(f"  where to look (best {w['k']}, floor): recall {w['recall']:.3f} on {w['with_evidence']} criteria with "
          f"evidence; {w['false_leads_per_criterion']:.2f} false leads per criterion without "
          f"({w['without_evidence']}), none shown for {w['none_shown']:.0%}")
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=1) + "\n")


if __name__ == "__main__":
    main()
