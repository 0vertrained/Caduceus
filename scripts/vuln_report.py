"""Summarize a Grype JSON report for CI: annotations on the run, a table in the job summary, and an exit code.

    python scripts/vuln_report.py grype.json --label "Container image" --fail-on fixed

--fail-on fixed  fail when any finding has a released fix (we can and must act on it)
--fail-on any    fail on every finding, fixed or not
--fail-on none   report only

Standard library only, so it runs before anything is installed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

SEVERITIES = ["Critical", "High", "Medium", "Low", "Negligible", "Unknown"]
MAX_ANNOTATIONS = 10  # GitHub shows at most 10 of each kind per step


def findings(report: dict) -> list[dict]:
    out = []
    for m in report.get("matches", []):
        vuln, art = m.get("vulnerability", {}), m.get("artifact", {})
        fix = vuln.get("fix") or {}
        out.append({
            "id": vuln.get("id", "?"),
            "severity": (vuln.get("severity") or "Unknown").capitalize(),
            "package": art.get("name", "?"),
            "version": art.get("version", "?"),
            "type": art.get("type", "?"),
            "fixed": fix.get("state") == "fixed",
            "fix_versions": fix.get("versions") or [],
        })
    order = {s: i for i, s in enumerate(SEVERITIES)}
    return sorted(out, key=lambda f: (not f["fixed"], order.get(f["severity"], 99), f["package"], f["id"]))


def describe(f: dict) -> str:
    fix = f"fixed in {', '.join(f['fix_versions'])}" if f["fixed"] else "no fix released"
    return f"{f['package']} {f['version']} ({f['type']}): {f['id']}, {f['severity']}, {fix}"


def escape(text: str) -> str:
    """Workflow-command escaping, so a finding cannot end its annotation early or inject another command."""
    return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def counts(items: list[dict]) -> str:
    c = Counter(f["severity"] for f in items)
    return ", ".join(f"{c[s]} {s.lower()}" for s in SEVERITIES if c[s]) or "none"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("report")
    ap.add_argument("--label", required=True)
    ap.add_argument("--fail-on", choices=["fixed", "any", "none"], default="fixed")
    args = ap.parse_args(argv)

    with open(args.report, encoding="utf-8") as fh:
        items = findings(json.load(fh))
    fixed = [f for f in items if f["fixed"]]
    unfixed = [f for f in items if not f["fixed"]]

    headline = (f"{len(items)} known vulnerabilities: {len(fixed)} with a released fix ({counts(fixed)}), "
                f"{len(unfixed)} without one ({counts(unfixed)})")
    print(f"::notice title={args.label}::{headline}")
    if items:  # the full list in one annotation, since each kind is capped at 10 per step
        listing = "%0A".join(escape(describe(f)) for f in items)
        print(f"::notice title={args.label}, every finding::{listing}")
    failing = {"fixed": fixed, "any": items, "none": []}[args.fail_on]
    for f in failing[:MAX_ANNOTATIONS]:
        print(f"::error title={args.label}::{escape(describe(f))}")
    for f in [f for f in items if f not in failing][:MAX_ANNOTATIONS]:
        print(f"::warning title={args.label}::{escape(describe(f))}")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### {args.label}\n\n{headline}.\n\n")
            if items:
                fh.write("| Package | Version | Type | Vulnerability | Severity | Fix |\n|---|---|---|---|---|---|\n")
                for f in items:
                    fix = ", ".join(f["fix_versions"]) if f["fixed"] else "none released"
                    cells = [f["package"], f["version"], f["type"], f["id"], f["severity"], fix]
                    fh.write("| " + " | ".join(cells) + " |\n")
                fh.write("\n")
    print(headline)
    for f in items:
        print("  " + describe(f))
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
