"""Record the external model's replies for the public demo, so a cold start replays them instead of calling it.

Runs the demo's story through the real pipeline on the de-identified route (drafts, attestations, approvals and an
appeal) and writes each reply keyed by its exact request, reusing replies already recorded. Only de-identified text
is ever sent, so the file holds no PHI.

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


def story(out: Path) -> tuple[list[str], list[str], dict[str, dict]]:
    """Run the demo's story once. Replies already recorded (in the file or earlier in this process) are reused."""
    svc = build_services(Settings(auth_mode="dev", db_path=":memory:", model_recordings=str(out), showcase="1"))
    if svc.router is None or not svc.router.external_enabled:
        raise SystemExit("No external model is configured: "
                         "set CADUCEUS_EXTERNAL_PROVIDER and CADUCEUS_EXTERNAL_API_KEY.")
    clock = [datetime.now(UTC)]
    svc.workflow.now = lambda: clock[0]
    seed(svc, now=clock[0])
    clinicians = {p.clinician_id: p for p in DEV_USERS.values() if p.clinician_id}
    for case in svc.cases.all():
        if case.effective_counts()["unknown"]:
            svc.workflow.request_attestation(DEV_USERS["dolores"], case.id)
            asked = svc.cases.get(case.id)
            svc.workflow.sign_attestation(clinicians[asked.ordering_clinician["id"]], case.id,  # type: ignore[union-attr]
                                          dict.fromkeys(asked.attestation_requested, "yes"))  # type: ignore[union-attr]
    for case in svc.cases.all():
        if case.ready_to_approve:
            svc.workflow.approve(DEV_USERS["dolores"], case.id)
    clock[0] += timedelta(seconds=31)  # past the undo window, so the outbox sends
    svc.workflow.dispatch_due()
    missed, reasons = [], []
    for c in svc.cases.all():
        for written in (c.draft or {}, c.appeal or {}):
            if written.get("route") == "template":
                missed.append(c.id)
                reasons.append(written.get("fallback_reason") or "?")
    return sorted(set(missed)), sorted(set(reasons)), svc.router.recordings()


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    out = Path(args[0])
    for _attempt in range(3):  # a dropped connection is retried; recorded replies are not asked for again
        missed, reasons, recorded = story(out)
        if not missed:
            break
    if not recorded:
        print(f"The model did not answer ({'; '.join(reasons)}); nothing was written.", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(recorded, indent=1, sort_keys=True) + "\n")
    print(f"{len(recorded)} replies from {next(iter(recorded.values()))['model']} recorded in {out}")
    if missed:  # those cases fall back to the template
        print(f"Not recorded, after 3 tries: {', '.join(missed)} ({'; '.join(reasons)})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
