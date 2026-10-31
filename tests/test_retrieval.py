"""Packet retrieval: clinical shorthand, OCR damage, and the queries criteria sets produce."""

from caduceus.retrieval import Passage, normalize, passages, search

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
