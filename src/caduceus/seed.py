"""Create the demo database: one case per scenario in data/cases/cases.json (and data/showcase with
CADUCEUS_SHOWCASE=1), prepared by the agent.

Clinical dates are fixed by the dataset (as of October 28, 2026). Received and due times are set relative to the
moment you seed, so there is always a mix of urgent, due-today and later work.

    python -m caduceus.seed                 # replaces var/caduceus.sqlite3
    python -m caduceus.seed --if-missing    # seeds only when there is no database yet (container start)

Stop the API server before seeding: seeding replaces the database file, and SQLite refuses writes from a
connection whose file has been replaced underneath it.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from caduceus.app_factory import Services, Settings, build_services, scenarios
from caduceus.cases import Case, Fax


def seed(services: Services, now: datetime | None = None, by_fax: bool = False) -> list[str]:
    """With by_fax, the referrals that have a faxed cover sheet (data/faxes) arrive in the intake inbox instead,
    to be read by the agent and confirmed by a person before they become cases."""
    now = now or datetime.now(UTC)
    data = services.settings.data_dir
    cases = scenarios(*services.settings.scenario_dirs())
    manifest = data / "faxes" / "faxes.json"
    faxed = ({m["expected"]["referral"]: (fax_id, m["file"]) for fax_id, m in json.loads(manifest.read_text()).items()}
             if by_fax and manifest.exists() and services.workflow.faxes else {})
    ids = []
    for c in cases:
        if c["id"] in faxed:
            fax_id, file = faxed[c["id"]]
            services.workflow.faxes.save(Fax(id=fax_id, file=file, pages=c["packet"], as_of=c["as_of"],  # type: ignore[union-attr]
                                             received_at=now - timedelta(minutes=20 * len(ids) + 5),
                                             due_in_hours=c["due_in_hours"] + 24))
            services.audit.append("intake", "agent_runtime", "fax.received", fax_id, {"pages": len(c["packet"]) + 1})
            continue
        # A new case runs under the criteria set its service uses now; the scenario names the one its labels were
        # written for, which the eval gate checks separately.
        criteria_set = services.workflow.catalog.get("services", {}).get(c["service"]["code"], {}).get(
            "criteria_set", c["criteria_set"])
        case = Case(id=c["id"], patient_id=c["patient_id"], service=c["service"], criteria_set=criteria_set,
                    side=c.get("side"), as_of=c["as_of"], payer=c["payer"], ordering_clinician=c["ordering_clinician"],
                    packet=c["packet"], received_at=now - timedelta(days=1),
                    due_at=now + timedelta(hours=c["due_in_hours"]))
        services.cases.save(case)
        services.audit.append("intake", "agent_runtime", "case.received", case.id,
                              {"service": case.service["code"], "pages": len(case.packet)})
        services.workflow.prepare(case.id)
        ids.append(case.id)
    return ids


def fresh_demo_services(settings: Settings) -> Services:
    """A new, seeded data set in memory, for the public demo's start and its scheduled resets. Seeding uses only
    recorded model replies (anything else falls back to the template): a cold start must never wait on a model."""
    services = build_services(replace(settings, db_path=":memory:"))
    with services.router.recorded_only() if services.router else nullcontext():
        seed(services, by_fax=True)
    return services


def clone_demo_services(template: Services, now: datetime, seeded_at: datetime) -> Services:
    """A visitor's own copy of a seeded template, made in milliseconds instead of seconds: the database is copied,
    then each case's received and due times move forward by the template's age, so the queue reads as new."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    template.conn.backup(conn)  # type: ignore[union-attr]
    services = build_services(replace(template.settings, db_path=":memory:"), conn=conn)
    services.workflow.now = template.workflow.now
    age = now - seeded_at
    for case in services.cases.all():
        services.cases.save(case.model_copy(update={"received_at": case.received_at + age,
                                                    "due_at": case.due_at + age}))
    return services


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    settings = Settings(auth_mode="dev")  # seeding signs no one in; the server chooses its own sign-in mode
    db = Path(settings.db_path)
    if db.exists():
        if "--if-missing" in args:
            print(f"{settings.db_path} already exists; leaving it as it is.")
            return
        db.unlink()
    services = build_services(settings)
    ids = seed(services, by_fax=True)
    print(f"Seeded {len(ids)} cases into {settings.db_path}; audit chain verified: {services.audit.verify()} events")


if __name__ == "__main__":
    main()
