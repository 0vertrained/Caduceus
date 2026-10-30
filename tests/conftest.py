from __future__ import annotations

import json
from pathlib import Path

import pytest

from caduceus.criteria.engine import Engine
from caduceus.criteria.loader import load_library, load_lock
from caduceus.fhir.store import LocalBundleStore

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


@pytest.fixture(scope="session")
def library():
    return load_library(DATA / "policies")


@pytest.fixture(scope="session")
def engine():
    return Engine(load_lock(DATA / "policies"))


@pytest.fixture(scope="session")
def store():
    return LocalBundleStore(DATA)


@pytest.fixture(scope="session")
def cases():
    return json.loads((DATA / "cases" / "cases.json").read_text())
