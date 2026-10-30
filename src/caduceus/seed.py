"""Create the demo database: one case per scenario in data/cases/cases.json, prepared by the agent.

Clinical dates are fixed by the dataset (as of October 28, 2026). Received and due times are set relative to the
moment you seed, so there is always a mix of urgent, due-today and later work.

    python -m caduceus.seed                 # replaces var/caduceus.sqlite3
    python -m caduceus.seed --if-missing    # seeds only when there is no database yet (container start)

Stop the API server before seeding: seeding replaces the database file, and SQLite refuses writes from a
connection whose file has been replaced underneath it.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from caduceus.app_factory import Services, Settings, build_services
from caduceus.cases import Case


def seed(services: Services, now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    cases = json.loads((services.settings.data_dir / "cases" / "cases.json").read_text())
    ids = []
    for c in cases:
        case = Case(id=c["id"], patient_id=c["patient_id"], service=c["service"], criteria_set=c["criteria_set"],
                    as_of=c["as_of"], payer=c["payer"], ordering_clinician=c["ordering_clinician"],
                    packet=c["packet"], received_at=now - timedelta(days=1),
                    due_at=now + timedelta(hours=c["due_in_hours"]))
        services.cases.save(case)
        services.audit.append("intake", "agent_runtime", "case.received", case.id,
                              {"service": case.service["code"], "pages": len(case.packet)})
        services.workflow.prepare(case.id)
        ids.append(case.id)
    return ids


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
    ids = seed(services)
    print(f"Seeded {len(ids)} cases into {settings.db_path}; audit chain verified: {services.audit.verify()} events")


if __name__ == "__main__":
    main()
