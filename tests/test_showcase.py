"""The public demo's showcase cases (data/showcase): each one shows what it was built to show, they are seeded
only when the showcase is on, and the committed data is exactly what the builders make."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from caduceus.app_factory import Settings, build_services
from caduceus.seed import seed
from tests.conftest import DATA, ROOT
from tests.test_workflow_api import T0

SHOWCASE = DATA / "showcase"
IDS = ["2026-1051", "2026-1052", "2026-1053"]
STORIES = {c["id"]: c for c in json.loads((SHOWCASE / "cases" / "cases.json").read_text())}


@pytest.fixture(scope="module")
def seeded():
    svc = build_services(Settings(data_dir=DATA, db_path=":memory:", auth_mode="dev", showcase="1"))
    svc.workflow.now = lambda: T0
    return svc, seed(svc, now=T0)


def test_the_showcase_is_seeded_after_the_dataset_and_only_when_it_is_on(seeded):
    _, ids = seeded
    assert ids[-3:] == IDS
    off = build_services(Settings(data_dir=DATA, db_path=":memory:", auth_mode="dev"))
    assert not set(IDS) & set(seed(off, now=T0))


@pytest.mark.parametrize("case_id", IDS)
def test_each_case_is_judged_the_way_its_story_says(seeded, case_id):
    svc, _ = seeded
    case = svc.cases.get(case_id)
    assert case.criteria_set == STORIES[case_id]["criteria_set"]  # the version its service runs under now
    assert {r["criterion_id"]: r["status"] for r in case.determination["results"]} == STORIES[case_id]["expected"]


def test_the_request_for_the_other_knee_is_caught(seeded):
    case = seeded[0].cases.get("2026-1051")
    assert case.side == "right"
    arthritis = next(r for r in case.determination["results"] if r["criterion_id"] == "arthritis_of_this_knee")
    assert "left knee is charted, but the request is for the right side" in arthritis["explanation"]


def test_where_to_look_points_at_the_outside_therapy_summary(seeded):
    case = seeded[0].cases.get("2026-1052")
    story = STORIES["2026-1052"]
    (gold,) = story["evidence"]["conservative_care"]
    first = case.leads["conservative_care"][0]
    assert first["doc_id"] == story["packet"][gold["page"] - 1]
    assert first["start"] < gold["end"] and gold["start"] < first["end"]
    assert set(case.leads) == {"conservative_care"}  # only what the record could not answer


def test_the_sleep_apnea_referral_arrives_expedited_with_the_clinics_reason(seeded):
    svc, _ = seeded
    case = svc.cases.get("2026-1053")
    assert case.urgency == "expedited" and "71%" in (case.urgency_reason or "")
    events = svc.audit.events(case_id="2026-1053")
    assert next(e for e in events if e.action == "case.received").detail["urgency"] == "expedited"
    assert not [e for e in events if "71%" in json.dumps(e.detail)]  # the audit log holds no clinical text


@pytest.mark.parametrize(("script", "committed"), [("build_showcase.py", SHOWCASE),
                                                   ("build_hard_set.py", DATA / "hard")])
def test_the_committed_data_is_what_the_builder_makes(tmp_path, script, committed):
    subprocess.run([sys.executable, str(ROOT / "scripts" / script), "--out", str(tmp_path)], check=True,  # noqa: S603
                   capture_output=True, cwd=ROOT)
    made = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    assert made == sorted(p.relative_to(committed) for p in committed.rglob("*") if p.is_file())
    assert [p for p in made if (tmp_path / p).read_bytes() != (committed / p).read_bytes()] == []
