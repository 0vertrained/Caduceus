"""Loads criteria sets from YAML and the reviewer's approval lock file."""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from caduceus.criteria.dsl import CriteriaSet

LOCK_NAME = "approved.lock.json"


def load_criteria_set(path: Path) -> CriteriaSet:
    return CriteriaSet.model_validate(yaml.safe_load(path.read_text()))


def load_library(directory: Path) -> dict[str, CriteriaSet]:
    sets = [load_criteria_set(p) for p in sorted(directory.glob("*.yaml"))]
    return {s.key: s for s in sets}


def load_lock(directory: Path) -> dict[str, str]:
    lock = directory / LOCK_NAME
    return json.loads(lock.read_text()) if lock.exists() else {}


def approve(directory: Path, key: str) -> str:
    """Record a reviewed set's content hash. Run by a human reviewer via `make approve-policy`."""
    library = load_library(directory)
    digest = library[key].content_sha256()
    lock = load_lock(directory)
    lock[key] = digest
    (directory / LOCK_NAME).write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    return digest


def for_service(library: dict[str, CriteriaSet], system: str, code: str) -> CriteriaSet | None:
    for s in library.values():
        if any((c.uri, c.code) == (system, code) or (c.system, c.code) == (system, code) for c in s.service_codes):
            return s
    return None
