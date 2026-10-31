"""Find the sentences in a referral packet that bear on a criterion, inside the boundary (ADR 5).

A packet is PHI, so nothing here calls a model or leaves the process. Each page is split into sentence passages,
and two channels rank them, fused by reciprocal rank:

1. Words, scored with BM25 after clinical normalization: abbreviations and synonyms become one concept token,
   so "PT x 8 wks", "physiotherapy" and "therapeutic exercise" all match "physical therapy".
2. Character trigrams, scored with BM25, which survive OCR damage ("rad1ographs") and words split across lines.

A third channel, dense embeddings from a model inside the VPC, plugs in through `embed`. None is configured yet:
the model hub is outside the boundary, and an embedding model must run inside it.

The concept list was written from general clinical usage before the evaluation packets existed, so that the
held-out score measures how it generalizes rather than how well it was fitted.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass

# Case-sensitive abbreviations first: "PT" is physical therapy, but "Pt" and "pt" mean the patient.
CASED: list[tuple[str, str]] = [
    (r"\bPT\b", "physicaltherapy"),
    (r"\bOA\b", "osteoarthritis"),
    (r"\bDJD\b", "osteoarthritis"),
    (r"\bJSN\b", "jointspacenarrowing"),
    (r"\bOSA\b|\bOSAS\b", "sleepapnea"),
    (r"\bPSG\b|\bHST\b|\bHSAT\b", "sleeptest"),
    (r"\bAHI\b|\bREI\b|\bRDI\b", "ahi"),
    (r"\bESS\b", "sleepiness"),
    (r"\bHTN\b", "hypertension"),
    (r"\bCAD\b|\bIHD\b", "ischemicheart"),
    (r"\bCVA\b|\bTIA\b", "stroke"),
    (r"\bXR\b", "xray"),
    (r"\bADLs?\b", "functionallimitation"),
    (r"\bLBP\b", "lowbackpain"),
]

# Everything else, matched on lower-cased text.
CONCEPTS: list[tuple[str, str]] = [
    (r"physical therap\w*|physiotherap\w*|therapeutic exercise\w*|physical medicine and rehab\w*", "physicaltherapy"),
    (r"x-?rays?\b|radiograph\w*|plain films?\b", "xray"),
    (r"magnetic resonance|\bmri\b", "mri"),
    (r"low back pain|lumbago|lumbar pain|lower back pain", "lowbackpain"),
    (r"radiculopath\w*|sciatica", "radiculopathy"),
    (r"red[- ]flags?", "redflag"),
    (r"cauda equina|saddle an(a)?esthesia|bowel or bladder|bladder or bowel|urinary retention|incontinen\w*|"
     r"unexplained weight loss|history of (cancer|malignancy)|night sweats", "redflag"),
    (r"osteoarthr\w*|degenerative joint disease|gonarthrosis", "osteoarthritis"),
    (r"joint[- ]space narrowing|narrow\w* (of )?(the )?(medial |lateral )?(joint|compartment)( space)?|"
     r"joint space\w* (is |was |are )?narrow\w*", "jointspacenarrowing"),
    (r"inject\w*|corticosteroid\w*|cortisone|steroid shot\w*|triamcinolone|kenalog|hyaluron\w*|"
     r"viscosupplement\w*|arthrocentesis", "jointinjection"),
    (r"activities of daily living|daily activities|functional (limitation|impairment|decline)\w*|"
     r"difficulty (with )?(walking|stairs|standing|climbing)", "functionallimitation"),
    (r"septic arthritis|joint infection|pyogenic|infect\w*|osteomyelitis|purulent", "infection"),
    (r"obstructive sleep apn(o)?ea|sleep apn(o)?ea", "sleepapnea"),
    (r"polysomnogra\w*|sleep stud(y|ies)|sleep test\w*|home sleep (apnea )?test\w*", "sleeptest"),
    (r"apn(o)?ea[- ]hypopn(o)?ea index|respiratory event index|respiratory disturbance index", "ahi"),
    (r"excessive daytime sleepiness|daytime sleepiness|hypersomnolence|epworth|somnolen\w*|"
     r"falls asleep (while|during)", "sleepiness"),
    (r"hypertension|hypertensive|high blood pressure", "hypertension"),
    (r"ischemic heart disease|coronary artery disease|coronary disease|myocardial infarction|angina", "ischemicheart"),
    (r"\bstroke\b|cerebrovascular accident", "stroke"),
    (r"face[- ]to[- ]face|in[- ]person|sleep (medicine )?(evaluation|consult\w*)", "facetoface"),
    (r"\bwks?\b|\bweeks?\b", "week"),
    (r"\bmos?\b|\bmonths?\b", "month"),
    (r"\byrs?\b|\byears?\b", "year"),
]

STOP = set("""a an and are as at be been but by for from has have had he her his in into is it its of on or our
she that the their them they this to was were which while who will with within before after than then there these
those patient patients pt per also any all not no yes""".split())

_CASED = [(re.compile(p), f" {t} ") for p, t in CASED]
_CONCEPTS = [(re.compile(p), f" {t} ") for p, t in CONCEPTS]
_WORD = re.compile(r"[a-z0-9]+")
_SENTENCE = re.compile(r"[^.!?\n]+(?:[.!?]+|\n|$)")


def _stem(w: str) -> str:
    for suffix, repl in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            return w[: -len(suffix)] + repl
    return w


def normalize(text: str) -> str:
    for rx, token in _CASED:
        text = rx.sub(token, text)
    text = text.lower()
    for rx, token in _CONCEPTS:
        text = rx.sub(token, text)
    return text


def words(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall(normalize(text)) if w not in STOP and len(w) > 1]


def trigrams(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(normalize(text)):
        if w in STOP:
            continue
        padded = f"#{w}#"
        out += [padded[i:i + 3] for i in range(len(padded) - 2)]
    return out


@dataclass(frozen=True)
class Passage:
    page: int  # 1-based, in packet order
    start: int  # character offsets in the page's text
    end: int
    text: str


@dataclass(frozen=True)
class Hit:
    passage: Passage
    score: float
    ranks: dict[str, int]


def passages(pages: Sequence[str]) -> list[Passage]:
    out = []
    for i, page in enumerate(pages, start=1):
        for m in _SENTENCE.finditer(page):
            text = m.group(0).strip()
            if len(_WORD.findall(text.lower())) >= 3:
                lead = len(m.group(0)) - len(m.group(0).lstrip())
                out.append(Passage(i, m.start() + lead, m.start() + lead + len(text), text))
    return out


def _bm25(docs: list[list[str]], query: list[str], k1: float = 1.2, b: float = 0.75) -> list[float]:
    n = len(docs)
    if n == 0:
        return []
    avg = sum(len(d) for d in docs) / n or 1.0
    df = Counter(t for d in docs for t in set(d))
    q = Counter(query)
    scores = []
    for d in docs:
        tf, length, s = Counter(d), len(d), 0.0
        for t in q:
            if tf[t]:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                s += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * length / avg))
        scores.append(s)
    return scores


def _ranks(scores: list[float]) -> list[int | None]:
    """1-based rank of each passage by score; passages that scored nothing get no rank."""
    order = sorted((i for i, s in enumerate(scores) if s > 0), key=lambda i: -scores[i])
    ranks: list[int | None] = [None] * len(scores)
    for r, i in enumerate(order, start=1):
        ranks[i] = r
    return ranks


def search(pages: Sequence[str], query: str, k: int = 5, channels: Sequence[str] = ("words", "trigrams"),
           embed: Callable[[list[str]], list[list[float]]] | None = None, rrf_k: int = 60) -> list[Hit]:
    """The k passages most likely to bear on the query, best first."""
    ps = passages(pages)
    if not ps:
        return []
    ranked: dict[str, list[int | None]] = {}
    if "words" in channels:
        ranked["words"] = _ranks(_bm25([words(p.text) for p in ps], words(query)))
    if "trigrams" in channels:
        ranked["trigrams"] = _ranks(_bm25([trigrams(p.text) for p in ps], trigrams(query)))
    if embed is not None:
        vectors = embed([query] + [p.text for p in ps])
        qv, pvs = vectors[0], vectors[1:]
        ranked["dense"] = _ranks([_cosine(qv, v) for v in pvs])
    fused = []
    for i, p in enumerate(ps):
        mine = {name: r[i] for name, r in ranked.items() if r[i] is not None}
        if mine:
            fused.append(Hit(p, sum(1 / (rrf_k + r) for r in mine.values()), mine))
    fused.sort(key=lambda h: (-h.score, h.passage.page, h.passage.start))
    return fused[:k]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return max(dot / (na * nb), 0.0) if na and nb else 0.0


def criterion_query(criterion) -> str:
    """The query for a criterion: only what the approved criteria set says, never anything about the case."""
    parts = [criterion.text, criterion.policy_sentence, criterion.attestation_question or "",
             criterion.attestation_help or ""]
    return " ".join(p for p in parts if p)
