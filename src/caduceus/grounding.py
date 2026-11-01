"""Checks that a sentence a model wrote says nothing its facts do not.

Deterministic and deliberately strict: a sentence passes only if every number in it, every word it negates and
every term it uses can be found in the facts it was written from. It cannot judge paraphrase, so a sentence that
fails is replaced with the record's own words rather than shown.
"""

from __future__ import annotations

import re

NEGATIONS = frozenset({"no", "not", "without", "never", "none", "nor", "neither", "denies", "denied", "absent",
                       "negative", "lacks", "lacking"})
NUMBER_WORDS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())} | {
    "thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80", "ninety": "90"}
# Words a summary may use whatever its facts say: grammar, and the vocabulary of saying what a chart shows.
GENERIC = frozenset("""
    a an the this that these those it its he she him her his hers they them their patient patients s
    is are was were be been being has have had do does did will would can could may might should
    and or but so as of for to in on at by with within from into over under about after before during since until
    than then also both each either per via up out which who whom whose what when where while
    chart charts record records document documents documented documentation note notes noted report reports
    reported show shows showed shown showing indicate indicates indicated indicating confirm confirms confirmed
    meet meets met meeting criterion criteria requirement requirements satisfy satisfies satisfied consistent
    evidence present recent recently prior previous previously current currently ongoing total including include
    includes period duration history findings finding result results there here
    underwent received completed tried performed
""".split())
_CLAUSE = re.compile(r"[.;:!?()\[\],\n]+")
_TOKEN = re.compile(r"[a-z0-9]+")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def unsupported(sentence: str, facts: str) -> str | None:
    """Why `sentence` says something `facts` do not ("number", "negation" or "term"), or None if it does not."""
    if _numbers(sentence) - _numbers(facts):
        return "number"
    negated_in_facts = _negated(facts)
    if any(not heads & negated_in_facts for heads in _negations(sentence)):
        return "negation"
    if _terms(sentence) - _terms(facts):
        return "term"
    return None


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(re.sub(r"n['’]t\b", " not", text.lower()))


def _stem(word: str) -> str:
    """A crude stem, applied to both sides, so "radiates", "radiated" and "radiating" compare equal."""
    for suffix, keep in (("ies", "y"), ("ing", ""), ("ed", ""), ("s", "")):
        if word.endswith(suffix) and not word.endswith("ss") and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)] + keep
            break
    return word[:-1] if word.endswith("e") and len(word) > 4 else word


def _numbers(text: str) -> set[str]:
    found = {n.lstrip("0") or "0" for n in _NUMBER.findall(text)}
    return found | {NUMBER_WORDS[t] for t in _tokens(text) if t in NUMBER_WORDS}


def _terms(text: str) -> set[str]:
    return {_stem(t) for t in _tokens(text)
            if not t.isdigit() and t not in GENERIC and t not in NEGATIONS and t not in NUMBER_WORDS}


def _negations(text: str) -> list[set[str]]:
    """For each negation, the terms it governs: the next two terms in its clause."""
    found = []
    for clause in _CLAUSE.split(text):
        tokens = _tokens(clause)
        for i, token in enumerate(tokens):
            if token in NEGATIONS:
                heads = [_stem(t) for t in tokens[i + 1:] if t not in GENERIC and t not in NEGATIONS][:2]
                if heads:
                    found.append(set(heads))
    return found


def _negated(text: str) -> set[str]:
    """Every term the text negates, within five words of the negation."""
    negated: set[str] = set()
    for clause in _CLAUSE.split(text):
        tokens = _tokens(clause)
        for i, token in enumerate(tokens):
            if token in NEGATIONS:
                negated |= {_stem(t) for t in tokens[i + 1:i + 6] if t not in GENERIC}
    return negated
