"""Find the sentences in a referral packet that bear on a criterion, inside the boundary (ADR 5).

Sentence passages are ranked by BM25 over words after clinical normalization ("PT x 8 wks" and "physiotherapy" both
mean physical therapy) and over character trigrams, which survive OCR damage, fused by reciprocal rank. Dense
embeddings plug in through `embed` once an embedding model runs inside the VPC.
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

STOP = {
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by", "for", "from", "has", "have", "had", "he", "her",
    "his", "in", "into", "is", "it", "its", "of", "on", "or", "our", "she", "that", "the", "their", "them", "they",
    "this", "to", "was", "were", "which", "while", "who", "will", "with", "within", "before", "after", "than", "then",
    "there", "these", "those", "patient", "patients", "pt", "per", "also", "any", "all", "not", "no", "yes",
}
# Words about documenting, not about the patient: criteria are full of them, and so is every signature line.
STOP |= {"clinician", "clinicians", "physician", "provider", "documents", "documented", "documentation", "record",
         "records", "chart", "request", "requested", "policy", "example"}

_CASED = [(re.compile(p), f" {t} ") for p, t in CASED]
_CONCEPTS = [(re.compile(p), f" {t} ") for p, t in CONCEPTS]
_WORD = re.compile(r"[a-z0-9]+")
# A sentence ends at . ! or ?, or at a line break that does not follow a hyphen: "evalu-\nation" is one word.
# A full stop inside a code or a number ("G47.33", "2.5 mg") does not end a sentence.
_SENTENCE = re.compile(r"(?:-\n|[^.!?\n]|[.!?](?=\S))+(?:[.!?]+(?=\s|$)|\n|$)")
_BROKEN = re.compile(r"(\w)-\s*\n\s*(\w)")
_UNITS = {"week", "month", "year"}
_CONCEPT_TOKENS = {t for _, t in CASED + CONCEPTS} - _UNITS  # durations are on every page; they name nothing


def _stem(w: str) -> str:
    for suffix, repl in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            return w[: -len(suffix)] + repl
    return w


def normalize(text: str) -> str:
    text = _BROKEN.sub(r"\1\2", text)
    for rx, token in _CASED:
        text = rx.sub(token, text)
    text = text.lower()
    for rx, token in _CONCEPTS:
        text = rx.sub(token, text)
    return text


def words(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall(normalize(text)) if w not in STOP and len(w) > 1]


def trigrams(text: str) -> list[str]:
    """Trigrams of the raw words (which survive OCR damage) and of any concept the text names."""
    raw = _WORD.findall(_BROKEN.sub(r"\1\2", text).lower())
    named = [w for w in _WORD.findall(normalize(text)) if w in _CONCEPT_TOKENS]
    out = []
    for w in raw + named:
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


def relevant(passage: Passage, query: str, background: str = "", subject: str = "") -> bool:
    """Enough in common with the query to show a person: a shared concept, or at least two shared words. What the
    background names (the service, which every page mentions) does not count, and a passage sharing only the subject
    ("Diagnosis: obstructive sleep apnea") names the request rather than answering a criterion."""
    shared = (set(words(passage.text)) & set(words(query))) - set(words(background)) - _UNITS
    if subject and not shared - set(words(subject)):
        return False
    return bool(shared & _CONCEPT_TOKENS) or len(shared) >= 2


def search(pages: Sequence[str], query: str, k: int = 5, channels: Sequence[str] = ("words", "trigrams"),
           embed: Callable[[list[str]], list[list[float]]] | None = None, rrf_k: int = 60,
           floor: bool = False, background: str = "", subject: str = "") -> list[Hit]:
    """The k passages most likely to bear on the query, best first. With `floor`, only passages relevant enough
    to show a person (see `relevant`)."""
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
        mine = {name: rank for name, ranks in ranked.items() if (rank := ranks[i]) is not None}
        if mine:
            fused.append(Hit(p, sum(1 / (rrf_k + r) for r in mine.values()), mine))
    fused.sort(key=lambda h: (-h.score, h.passage.page, h.passage.start))
    seen, unique = set(), []
    for h in fused:  # a line copied forward onto many pages takes one place in the results, not several
        key = " ".join(words(h.passage.text))
        if key not in seen and (not floor or relevant(h.passage, query, background, subject)):
            seen.add(key)
            unique.append(h)
    return unique[:k]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(y * y for y in b))
    return max(dot / (na * nb), 0.0) if na and nb else 0.0


def criterion_query(criterion) -> str:
    """The query for a criterion: only what the approved criteria set says, never anything about the case."""
    parts = [criterion.text, criterion.policy_sentence, criterion.attestation_question or "",
             criterion.attestation_help or ""]
    return " ".join(p for p in parts if p)


def where_to_look(pages: Sequence[str], criteria_set, criterion, k: int = 3) -> list[Hit]:
    """The best k passages for one criterion that clear the relevance floor. What the service names is on every page,
    so it counts only where the criterion names it too, and never on its own."""
    named = set(words(criterion.text))
    about = " ".join([criteria_set.title] + [c.display or "" for c in criteria_set.service_codes])
    background = " ".join(w for w in words(about) if w not in named)
    return search(pages, criterion_query(criterion), k, floor=True, background=background, subject=about)
