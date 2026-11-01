"""The check that keeps the in-network model's summary to what the record says. The failing sentences are ones the
model wrote in recordings of the walkthrough; the passing ones are faithful rewordings of the same facts."""

import pytest

from caduceus.grounding import unsupported

PATIENT = "Patient: Kristina Mante, 72-year-old female."
THERAPY = PATIENT + """
Conservative treatment tried first
Physical therapy for 6 weeks, July 2, 2026 to August 15, 2026.
Source: "Patient completed 6 weeks of physical therapy with minimal improvement in pain or function."
"""
XRAY = PATIENT + """
Plain X-ray within the last 90 days
Lumbar spine X-ray on September 1, 2026, 8 weeks ago.
Source: "Degenerative disc disease at L4 to L5 with mild narrowing. No acute fracture or malalignment."
"""
PAIN = PATIENT + """
Low back pain for at least six weeks
Documented for 9 weeks, since August 26, 2026.
Source: "Patient reports low back pain radiating to the left buttock, ongoing for approximately 9 weeks."
"""


@pytest.mark.parametrize(("sentence", "facts"), [
    ("Kristina Mante completed 6 weeks of physical therapy, from July 2 to August 15, 2026, with minimal "
     "improvement in pain or function.", THERAPY),
    ("A lumbar spine X-ray on September 1, 2026 showed degenerative disc disease at L4 to L5 with mild narrowing "
     "and no acute fracture.", XRAY),
    ("Low back pain radiating to the left buttock has been documented for nine weeks, since August 26, 2026.", PAIN),
    ("She has had low back pain for at least six weeks.", PAIN),
])
def test_a_faithful_sentence_passes(sentence, facts):
    assert unsupported(sentence, facts) is None


@pytest.mark.parametrize(("sentence", "facts", "why"), [
    # "Minimal improvement" became none at all.
    ("She tried conservative treatment (physical therapy) for 6 weeks without improvement.", THERAPY, "negation"),
    # An MRI the record never mentions, in a criterion about an X-ray.
    ("She had a plain X-ray showing degenerative disc disease and has not had an MRI within the last 90 days.",
     XRAY, "negation"),
    ("A lumbar spine X-ray and an MRI were done within the last 90 days.", XRAY, "term"),
    # A number the facts do not have.
    ("Physical therapy lasted 8 weeks.", THERAPY, "number"),
    ("Low back pain has been documented for twelve weeks.", PAIN, "number"),
    # A finding the facts do not have.
    ("The X-ray showed severe narrowing at L4 to L5.", XRAY, "term"),
])
def test_an_embellished_sentence_is_caught(sentence, facts, why):
    assert unsupported(sentence, facts) == why


def test_a_negation_the_facts_share_passes_but_one_they_do_not_fails():
    assert unsupported("There was no acute fracture or malalignment.", XRAY) is None
    assert unsupported("There was no degenerative disc disease.", XRAY) == "negation"
    assert unsupported("Her pain hasn't improved.", THERAPY) == "negation"
