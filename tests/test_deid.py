"""Safe Harbor de-identification and stamps."""

from __future__ import annotations

from datetime import date

import pytest

from caduceus.deid.safe_harbor import (
    DeidResult,
    PatientContext,
    SafeHarborDeidentifier,
    StampedText,
    reidentify,
)

CTX = PatientContext(
    names=["Zebediah Quillfeather", "Marisol Quillfeather"],
    birth_date=date(1954, 3, 14),
    address_lines=["1742 Calle Ventana"],
    city="Santa Fe",
    postal_code="87505",
    phones=["505-555-0143"],
    emails=["zq@example.net"],
    record_numbers=["MV-0099812", "HUM884417", "123-45-6789"],
)

NOTE = (
    "Zebediah Quillfeather (DOB 03/14/1954, MRN MV-0099812) lives at 1742 Calle Ventana, Santa Fe, NM 87505 "
    "with his wife Marisol. Call 505-555-0143 or zq@example.net. Member ID HUM884417, SSN 123-45-6789. "
    "Seen March 14, 2026 and again on 2026-08-14 and 9/2/2026. Portal: https://portal.example.org/zq "
    "from 10.1.2.3. His 92-year-old father also had back pain. Requested CPT 72148 for M54.50; "
    "AHI 22 events per hour after 6 weeks of therapy."
)

PLANTED = ["Zebediah", "Quillfeather", "Marisol", "03/14/1954", "MV-0099812", "1742 Calle Ventana", "Santa Fe",
           "87505", "505-555-0143", "zq@example.net", "HUM884417", "123-45-6789", "March 14, 2026", "2026-08-14",
           "9/2/2026", "portal.example.org", "10.1.2.3", "92-year-old"]


def test_removes_every_planted_identifier(deid):
    out = deid.deidentify(NOTE, CTX)
    for value in PLANTED:
        assert value not in out.text, value
    assert "90 or older" in out.text
    assert {"NAME", "DATE", "PHONE", "EMAIL", "RECORD_NUMBER", "ADDRESS", "URL", "IP"} <= set(out.removed)


@pytest.mark.parametrize("text,leak", [
    ("Daughter can be reached at (505) 555-0199.", "555-0199"),
    ("Neighbor's cell 505.555.0177 is on file.", "555.0177"),
    ("Records were faxed to +1 505 555 0188.", "555 0188"),
    ("Old SSN 987-65-4320 appears on the scanned card.", "987-65-4320"),
    ("Send results to caregiver@mail.example.com.", "caregiver@mail.example.com"),
    ("Policy number QX7781203 for the secondary plan.", "QX7781203"),
    ("Visit on Sept 3, 2025 was cancelled.", "Sept 3"),
    ("Lives at 88 Camino Real Road with family.", "88 Camino Real Road"),
    ("Mailing address ends Albuquerque, NM 87102.", "87102"),
    ("Patient is a 101 year old veteran.", "101 year"),
])
def test_patterns_catch_identifiers_not_known_in_advance(deid, text, leak):
    """The pattern layer must work on its own: these identifiers are not in the case context."""
    out = deid.deidentify(text, PatientContext())
    assert leak not in out.text


def test_keeps_clinical_content(deid):
    out = deid.deidentify(NOTE, CTX)
    for keep in ["CPT 72148", "M54.50", "AHI 22", "6 weeks", "back pain"]:
        assert keep in out.text, keep


def test_reidentifies_inside_the_vpc(deid):
    out = deid.deidentify(NOTE, CTX)
    assert "Zebediah Quillfeather" in reidentify(out.text, out.token_map)


def test_same_value_same_token_across_calls(deid):
    first = deid.deidentify("Zebediah Quillfeather was seen.", CTX)
    second = deid.deidentify("Plan for Zebediah Quillfeather.", CTX, tokens=first.token_map)
    token = next(t for t, v in first.token_map.items() if v == "Zebediah Quillfeather")
    assert token in second.text


def test_residual_check_finds_survivors(deid):
    assert deid.residual_identifiers("Plan for zebediah.", CTX) == ["Zebediah"]
    assert deid.residual_identifiers("Plan for [NAME_1].", CTX) == []


def test_stamp_verifies_and_detects_tampering(deid):
    stamped = deid.stamp(deid.deidentify(NOTE, CTX))
    assert deid.verify(stamped)
    assert not deid.verify(StampedText(stamped.text + " Zebediah", stamped.stamp))
    assert not SafeHarborDeidentifier(b"a-different-key-also-at-least-32-bytes!!").verify(stamped)
    assert not deid.verify("plain string")  # type: ignore[arg-type]


def test_short_signing_key_rejected():
    with pytest.raises(ValueError):
        SafeHarborDeidentifier(b"short")


def test_stamp_covers_exact_text(deid):
    result = DeidResult("[NAME_1] has back pain.", {}, {})  # type: ignore[arg-type]
    stamped = deid.stamp(result)
    assert stamped.stamp.text_sha256 and stamped.stamp.policy == "safe-harbor/1"


def test_a_labelled_record_number_needs_a_digit_so_prose_after_a_label_survives():
    from caduceus.deid.safe_harbor import PatientContext, SafeHarborDeidentifier
    deid = SafeHarborDeidentifier(b"k" * 32)
    out = deid.deidentify("Coverage policy sentence: covered. Policy number: HX4471209. Member ID AB12345.",
                          PatientContext(names=[]))
    assert "policy sentence" in out.text and "HX4471209" not in out.text and "AB12345" not in out.text
    assert out.removed["RECORD_NUMBER"] == 2


def test_the_fixed_text_prompts_are_built_from_loses_nothing():
    """Words that carry no PHI (the facts' labels, every criterion and policy sentence) reach the model as written:
    the leakage suite checks under-removal, and this checks over-removal by a pattern's false positive."""
    from caduceus.criteria.loader import load_library
    from caduceus.deid.safe_harbor import PatientContext, SafeHarborDeidentifier
    from tests.conftest import DATA

    labels = ["Patient:", "Requested service:", "Criteria:", "Source:", "Payer:", "Payer's reason for denial:",
              "Criterion the payer cited:", "Coverage policy sentence:", "What the record shows:",
              "awaiting clinician attestation", "met.", "not met."]
    library = load_library(DATA / "policies")
    texts = labels + [t for s in library.values() for c in s.criteria
                      for t in (c.text, c.policy_sentence, c.attestation_question or "")]
    deid = SafeHarborDeidentifier(b"k" * 32)
    removed = {t: dict(out.removed) for t in texts if (out := deid.deidentify(t, PatientContext(names=[]))).removed}
    assert removed == {}
