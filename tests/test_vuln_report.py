"""The CI vulnerability summary: what fails the build and what is only reported."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "vuln_report", Path(__file__).resolve().parents[1] / "scripts" / "vuln_report.py")
vuln_report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vuln_report)


def match(pkg: str, severity: str, state: str, versions: list[str] | None = None) -> dict:
    return {"vulnerability": {"id": f"CVE-2026-{abs(hash(pkg)) % 10000}", "severity": severity,
                              "fix": {"state": state, "versions": versions or []}},
            "artifact": {"name": pkg, "version": "1.0", "type": "deb"}}


@pytest.fixture()
def report(tmp_path):
    def write(*matches):
        p = tmp_path / "grype.json"
        p.write_text(json.dumps({"matches": list(matches)}))
        return str(p)
    return write


def test_a_clean_report_passes(report, capsys):
    assert vuln_report.main([report(), "--label", "Image"]) == 0
    assert "0 known vulnerabilities" in capsys.readouterr().out


def test_any_fixable_finding_fails_even_when_low(report, capsys):
    path = report(match("libfoo", "Low", "fixed", ["1.1"]), match("libbar", "High", "not-fixed"))
    assert vuln_report.main([path, "--label", "Image", "--fail-on", "fixed"]) == 1
    out = capsys.readouterr().out
    assert "::error title=Image::libfoo 1.0 (deb)" in out and "fixed in 1.1" in out
    assert "::warning title=Image::libbar 1.0 (deb)" in out and "no fix released" in out


def test_unfixed_findings_are_reported_not_failed_by_default(report):
    assert vuln_report.main([report(match("libbar", "Critical", "wont-fix")), "--label", "Image"]) == 0


def test_fail_on_any_and_none(report):
    path = report(match("libbar", "Medium", "not-fixed"))
    assert vuln_report.main([path, "--label", "Image", "--fail-on", "any"]) == 1
    assert vuln_report.main([report(match("x", "High", "fixed", ["2"])), "--label", "J", "--fail-on", "none"]) == 0


def test_job_summary_lists_every_finding(report, tmp_path, monkeypatch):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    vuln_report.main([report(match("libfoo", "Low", "fixed", ["1.1"]), match("libbar", "High", "not-fixed")),
                      "--label", "Image", "--fail-on", "none"])
    text = summary.read_text()
    assert "| libfoo | 1.0 | deb |" in text and "| libbar | 1.0 | deb |" in text and "none released" in text


def test_every_finding_is_listed_in_one_annotation_and_escaped(report, capsys):
    findings = [match(f"lib{i}", "Low", "not-fixed") for i in range(15)]
    findings.append(match("evil%0A::error::injected", "Low", "not-fixed"))
    vuln_report.main([report(*findings), "--label", "Image", "--fail-on", "none"])
    every = [line for line in capsys.readouterr().out.splitlines() if "every finding::" in line][0]
    assert every.count("%0A") == 15  # 16 findings, one line each
    assert "evil%250A::error::injected" in every  # a crafted name cannot start a new workflow command
