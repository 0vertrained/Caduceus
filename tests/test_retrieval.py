"""Packet retrieval: clinical shorthand, OCR damage, and the queries criteria sets produce."""

from caduceus.retrieval import Passage, normalize, passages, search, where_to_look

PAGES = [
    "Fax cover sheet. Pages: 4. Confidential health information enclosed.",
    "Pt reports low back pain for 3 months. Pt denies fever. Plan: follow up in two weeks.",
    "Therapy discharge. Completed PT x 8 wks, 2x/week, with minimal improvement in function.",
    "Chest X-ray: no acute cardiopulmonary disease.\nLumbar spine rad1ographs, 2 views: mild disc space narrowing.",
]


def test_passages_keep_the_page_and_exact_offsets():
    for p in passages(PAGES):
        assert PAGES[p.page - 1][p.start:p.end] == p.text


def test_pt_in_capitals_is_physical_therapy_but_pt_for_patient_is_not():
    assert "physicaltherapy" in normalize("Completed PT x 8 wks")
    assert "physicaltherapy" not in normalize("Pt reports low back pain")


def test_shorthand_for_physical_therapy_is_found_first():
    hits = search(PAGES, "At least six weeks of conservative care, such as physical therapy")
    assert hits[0].passage.page == 3


def test_ocr_damage_and_the_right_body_part_win_over_a_chest_film():
    hits = search(PAGES, "Plain radiographs of the lumbar spine were obtained within 90 days")
    assert hits[0].passage.text.startswith("Lumbar spine rad1ographs")


def test_a_dense_channel_is_fused_when_one_is_configured():
    def embed(texts):  # a stand-in: pages mentioning "therapy" point the same way as the query
        return [[1.0, 0.0] if "therapy" in t.lower() or "PT" in t else [0.0, 1.0] for t in texts]

    hits = search(PAGES, "physical therapy", embed=embed)
    assert "dense" in hits[0].ranks and hits[0].passage.page == 3


def test_an_empty_packet_returns_nothing():
    assert search([], "anything") == [] and search([""], "anything") == []
    assert isinstance(passages(PAGES)[0], Passage)


def test_a_word_broken_across_a_line_stays_one_word_in_one_sentence():
    page = "Face-to-face sleep evalu-\nation on 09/27/2025: loud snoring.\nLungs clear."
    first = passages([page])[0]
    assert "evalu-\nation" in first.text and "facetoface" in normalize(first.text)


def test_a_signature_line_does_not_outrank_the_evidence():
    pages = ["Electronically signed by the attending clinician.\n" * 3,
             "Functional assessment: unable to complete daily activities without stopping."]
    hits = search(pages, "The clinician documents pain or loss of function that limits activities of daily living")
    assert hits[0].passage.page == 2
    assert len([h for h in hits if h.passage.page == 1]) <= 1  # a copied-forward line takes one place


def test_a_full_stop_inside_a_code_does_not_end_the_sentence():
    texts = [p.text for p in passages(["Diagnosis: G47.33, obstructive sleep apnea. Plan: 2.5 mg nightly."])]
    assert texts == ["Diagnosis: G47.33, obstructive sleep apnea.", "Plan: 2.5 mg nightly."]


def test_the_floor_hides_passages_that_share_only_what_every_page_says():
    pages = ["Requested service: CPAP device for sleep apnea.", "Sleep evaluation in person: loud snoring.",
             "Follow up in two weeks."]
    query = "A clinician evaluated the patient in person for sleep apnea before the sleep test."
    shown = [h.passage.page for h in search(pages, query, floor=True, background="CPAP device sleep apnea")]
    assert shown == [2]  # the service line names sleep apnea on every page; a duration names nothing


def test_where_to_look_skips_a_passage_that_only_names_what_the_service_is_about(library):
    cpap = library["cpap-osa@2026-11"]
    severity = next(c for c in cpap.criteria if c.id == "severity_qualifies")
    pages = ["Requested service: CPAP device for sleep apnea (HCPCS E0601).",
             "Diagnosis: G47.33, obstructive sleep apnea.", "Apnea-hypopnea index 9 events per hour."]
    # The criterion names sleep apnea, so it is not background; but a passage sharing nothing else names the request.
    assert [h.passage.page for h in where_to_look(pages, cpap, severity)] == [3]
