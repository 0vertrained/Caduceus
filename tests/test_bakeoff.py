"""The bake-off's deterministic checks. A wrong rule misjudges a model, so the rules get tests like the product."""

from scripts.bakeoff import rules

FACTS = (
    "Payer's reason for denial: The apnea-hypopnea index of 9 is below 15 events per hour.\n"
    'Coverage policy sentence: "The apnea-hypopnea index is 15 or more, or it is between 5 and 14."\n'
)


def test_a_quote_with_typographic_hyphens_and_quotes_still_counts_as_a_quote():
    reply = ("The denial states: “The apnea‑hypopnea index of 9 is below 15 events per hour.” The policy "
             "requires: “The apnea‑hypopnea index is 15 or more, or it is between 5‑14.”")
    out = rules({"task": "draft_appeal", "user": FACTS}, reply)
    assert out["quotes_reason"] and out["quotes_policy"]


def test_an_appeal_that_paraphrases_does_not_count_as_quoting():
    out = rules({"task": "draft_appeal", "user": FACTS}, "The index was too low according to the payer.")
    assert not out["quotes_reason"] and not out["quotes_policy"]


def test_saying_every_criterion_is_met_while_one_is_pending_is_flagged():
    facts = "Criteria:\n- Pain limits daily activities: awaiting clinician attestation. None found."
    assert rules({"task": "draft_request", "user": facts}, "She meets the documented criteria.")["misstates_criteria"]
    assert not rules({"task": "draft_request", "user": facts}, "One criterion awaits attestation.")[
        "misstates_criteria"]
