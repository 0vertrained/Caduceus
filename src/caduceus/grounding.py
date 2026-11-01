"""Checks that a sentence a model wrote says nothing its facts do not.

Deterministic and deliberately strict: a sentence passes only if every number in it, everything it negates and
every term it uses can be found in the facts it was written from. It cannot judge paraphrase, so a sentence that
fails is replaced with the record's own words rather than shown.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

# "No fever", "denies cancer": the negation governs what follows it, up to a word that ends its scope.
NEGATIONS = frozenset("no not without never none nor neither denies denied lacks lacking absence".split())
SCOPE_ENDS = frozenset("but however although though yet except while whereas which who reports reported".split())
# "Screen: negative", "pulses absent", "ruled out": a result that negates what it is about, on either side.
RESULTS = frozenset({"negative", "absent"})
NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())} | {
    "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90"}
# Words a summary may use whatever its facts say: grammar, and the vocabulary of saying what a chart shows. Compared
# by stem, so their other forms are covered too.
GENERIC = frozenset("""
    a an the this that these those it its he she him her his hers they them their patient s
    is are was were be been being has have had do does did will would can could may might should
    and or but so as of for to in on at by with within from into over under about after before during since until
    than then also both each either per via up out which who whom whose what when where while all any some most
    specifically including include there here
    chart record document documentation note report show indicate confirm identify find observe see date
    meet criterion criteria requirement satisfy consistent evidence present recent recently prior previous
    previously current currently ongoing total period duration history finding result
    undergo underwent undergone receive complete try tried perform conduct administer initiate continue attempt
    make made experience last start begin began symptom imaging diagnose diagnosis
""".split())
_SENTENCE = re.compile(r"[.;!?()\[\]\n]+")
_TOKEN = re.compile(r"[a-z0-9]+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def unsupported(sentence: str, facts: str) -> str | None:
    """Why `sentence` says something `facts` do not ("number", "negation" or "term"), or None if it does not."""
    return next(iter(findings(sentence, facts)), None)


def findings(sentence: str, facts: str) -> dict[str, list[str]]:
    """What in `sentence` the facts do not support, by kind, most serious first; empty if nothing.

    A negation the facts do not share is unsupported, and so is stating what the facts only ever negate.
    """
    _, fact_negated, fact_stated = _polarity(facts, reach=12)
    claims, _, _ = _polarity(sentence, reach=2)  # what each of the sentence's negations hinges on
    _, _, said = _polarity(sentence, reach=12)  # what it states outside any negation
    found = {"number": sorted(_numbers(sentence) - _numbers(facts)),
             "negation": sorted({" ".join(sorted(claim)) for claim in claims if not claim & fact_negated}
                                | ((said & fact_negated) - fact_stated)),
             "term": sorted(_terms(sentence) - _terms(facts))}
    return {kind: items for kind, items in found.items() if items}


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(re.sub(r"n['’]t\b", " not", text.lower()))


def _stem(word: str) -> str:
    """A crude stem, applied to both sides, so "radiates", "radiated" and "radiating" compare equal."""
    for suffix, keep in (("ies", "y"), ("ied", "y"), ("ing", ""), ("ed", ""), ("s", "")):
        if word.endswith(suffix) and not word.endswith("ss") and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)] + keep
            break
    return word[:-1] if word.endswith("e") and len(word) > 3 else word


GENERIC_STEMS = frozenset(_stem(w) for w in GENERIC)


def _is_term(token: str) -> bool:
    return (not token.isdigit() and token not in NUMBER_WORDS and token not in NEGATIONS
            and token not in GENERIC and _stem(token) not in GENERIC_STEMS)


def _numbers(text: str) -> set[str]:
    found = {n.lstrip("0") or "0" for n in _NUMBER.findall(text)}
    return found | {NUMBER_WORDS[t] for t in _tokens(text) if t in NUMBER_WORDS}


def _terms(text: str) -> set[str]:
    return {_stem(t) for t in _tokens(text) if _is_term(t)}


def _scopes(tokens: list[str], reach: int) -> Iterator[list[int]]:
    """For each negation in a sentence, the positions of the terms it governs: up to `reach` terms after a
    negating word, within its scope; or, for a negative result, what it is about: up to twelve terms before it,
    and up to three after "ruled out", "negative for" or "negative" naming what it describes."""
    def terms(positions: range, limit: int) -> list[int]:
        found: list[int] = []
        for j in positions:
            if len(found) == limit or tokens[j] in SCOPE_ENDS:
                break
            if _is_term(tokens[j]) and tokens[j] not in RESULTS:
                found.append(j)
        return found

    for i, word in enumerate(tokens):
        if word in NEGATIONS:
            yield terms(range(i + 1, len(tokens)), reach)
        elif word in RESULTS or (word == "ruled" and tokens[i + 1:i + 2] == ["out"]):
            following = tokens[i + 1] if i + 1 < len(tokens) else ""
            if following in ("out", "for"):
                after = terms(range(i + 2, len(tokens)), 3)
            elif _is_term(following):
                after = terms(range(i + 1, len(tokens)), 3)
            else:
                after = []
            yield terms(range(i - 1, -1, -1), 12) + after


def _polarity(text: str, reach: int) -> tuple[list[set[str]], set[str], set[str]]:
    """The text's negation scopes, the terms it negates, and the terms it states outside any negation."""
    scopes: list[set[str]] = []
    negated: set[str] = set()
    stated: set[str] = set()
    for sentence in _SENTENCE.split(text):
        tokens = _tokens(sentence)
        covered: set[int] = set()
        for positions in _scopes(tokens, reach):
            if positions:
                scopes.append({_stem(tokens[j]) for j in positions})
                covered.update(positions)
        for j, token in enumerate(tokens):
            if _is_term(token):
                (negated if j in covered else stated).add(_stem(token))
    return scopes, negated, stated
