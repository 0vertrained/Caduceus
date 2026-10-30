"""Record the external model's replies for the public demo, so it can start without calling the model.

Runs the demo's story through the real pipeline with a live model on the de-identified route: every case is
seeded and drafted, every case with an unanswered criterion is attested "yes" by its ordering clinician and drafted
again, and every case ready to send is approved and sent, so a denial's appeal is drafted too. Every reply is
written to a JSON file, keyed by the exact request (model, instructions, text and limit). Replies already in the
file are reused, not requested again. Only de-identified text was ever sent, so the file holds no PHI, and it
records exactly what went out and what came back. The demo's deploy runs this before it builds the image, so a cold
start replays these replies instead of calling the model.

    CADUCEUS_EXTERNAL_PROVIDER=ollama CADUCEUS_EXTERNAL_API_KEY=... \\
        python scripts/record_completions.py data/recorded/completions.json
    CADUCEUS_EXTERNAL_PROVIDER=stub python scripts/record_completions.py /tmp/stub.json   # tests only
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from caduceus.app_factory import Settings, build_services
from caduceus.auth import DEV_USERS
from caduceus.seed import seed


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(args[0])
    svc = build_services(Settings(auth_mode="dev", db_path=":memory:", model_recordings=str(out)))
    if svc.router is None or not svc.router.external_enabled:
        print("No external model is configured: set CADUCEUS_EXTERNAL_PROVIDER and CADUCEUS_EXTERNAL_API_KEY.",
              file=sys.stderr)
        return 1
    clock = [datetime.now(UTC)]
    svc.workflow.now = lambda: clock[0]
    seed(svc, now=clock[0])
    clinicians = {p.clinician_id: p for p in DEV_USERS.values() if p.clinician_id}
    for case in svc.cases.all():
        if case.effective_counts()["unknown"]:
            svc.workflow.request_attestation(DEV_USERS["dolores"], case.id)
            asked = svc.cases.get(case.id)
            svc.workflow.sign_attestation(clinicians[asked.ordering_clinician["id"]], case.id,  # type: ignore[union-attr]
                                          {c: "yes" for c in asked.attestation_requested})  # type: ignore[union-attr]
    for case in svc.cases.all():
        if case.ready_to_approve:
            svc.workflow.approve(DEV_USERS["dolores"], case.id)
    clock[0] += timedelta(seconds=31)  # past the undo window, so the outbox sends
    svc.workflow.dispatch_due()
    missed = sorted(c.id for c in svc.cases.all()
                    if (c.draft or {}).get("route") == "template" or (c.appeal or {}).get("route") == "template")
    if missed:
        reasons = sorted({(c.draft or {}).get("fallback_reason") or (c.appeal or {}).get("fallback_reason") or "?"
                          for c in svc.cases.all() if c.id in missed})
        print(f"The model did not answer for {', '.join(missed)} ({'; '.join(reasons)}); nothing was written.",
              file=sys.stderr)
        return 1
    recorded = svc.router.recordings()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(recorded, indent=1, sort_keys=True) + "\n")
    print(f"{len(recorded)} replies from {next(iter(recorded.values()))['model']} recorded in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
