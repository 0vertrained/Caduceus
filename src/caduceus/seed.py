"""Create the demo database: a case per scenario, prepared by the agent, with received and due times relative to now.

Stop the API server first: seeding replaces the SQLite file, and SQLite refuses writes from a connection whose file
was replaced. Postgres is seeded only while it holds no cases, since its audit log cannot be cleared.

    python -m caduceus.seed                 # replaces var/caduceus.sqlite3
    python -m caduceus.seed --if-missing    # seeds only when there is no database yet (container start)
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
    inbox = services.workflow.faxes if by_fax and manifest.exists() else None
    faxed = ({m["expected"]["referral"]: (fax_id, m["file"]) for fax_id, m in json.loads(manifest.read_text()).items()}
             if inbox else {})
    ids: list[str] = []
    for c in cases:
        if inbox is not None and c["id"] in faxed:
            fax_id, file = faxed[c["id"]]
            inbox.save(Fax(id=fax_id, file=file, pages=c["packet"], as_of=c["as_of"],
                           received_at=now - timedelta(minutes=20 * len(ids) + 5), due_in_hours=c["due_in_hours"] + 24))
            services.audit.append("intake", "agent_runtime", "fax.received", fax_id, {"pages": len(c["packet"]) + 1})
            continue
        # The criteria set the service runs under now; the scenario names the one its labels were written for.
        criteria_set = services.workflow.catalog.get("services", {}).get(c["service"]["code"], {}).get(
            "criteria_set", c["criteria_set"])
        case = Case(id=c["id"], patient_id=c["patient_id"], service=c["service"], criteria_set=criteria_set,
                    side=c.get("side"), urgency=c.get("urgency", "standard"), urgency_reason=c.get("urgency_reason"),
                    as_of=c["as_of"], payer=c["payer"], ordering_clinician=c["ordering_clinician"],
                    packet=c["packet"], received_at=now - timedelta(days=1),
                    due_at=now + timedelta(hours=c["due_in_hours"]))
        services.cases.save(case)
        services.audit.append("intake", "agent_runtime", "case.received", case.id,
                              {"service": case.service["code"], "pages": len(case.packet), "urgency": case.urgency})
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


def seed_postgres(settings: Settings, if_missing: bool) -> None:
    """Seed the Postgres database the settings name, if it holds no cases or faxes yet."""
    services = build_services(settings)
    try:
        if services.cases.all() or (services.workflow.faxes and services.workflow.faxes.all()):
            if if_missing:
                print("The database already holds cases; leaving it as it is.")
                return
            raise SystemExit("The database already holds cases, and its audit log cannot be cleared. Seed a new, "
                             "empty database instead.")
        ids = seed(services, by_fax=True)
        print(f"Seeded {len(ids)} cases into Postgres; audit chain verified: {services.audit.verify()} events")
    finally:
        services.close()


def main(argv: list[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else argv
    settings = Settings(auth_mode="dev")  # seeding signs no one in; the server chooses its own sign-in mode
    if settings.database_url:
        seed_postgres(settings, if_missing="--if-missing" in args)
        return
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
