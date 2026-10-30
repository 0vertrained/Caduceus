"""scripts/offline.sh: what Synthea runs inside. Nothing outside may be reachable; the command still runs."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

import pytest

OFFLINE = Path(__file__).resolve().parents[1] / "scripts" / "offline.sh"


def isolation_available() -> bool:
    if platform.system() != "Linux" or not shutil.which("unshare"):
        return False
    if os.geteuid() == 0:
        return True
    checks = (["sudo", "-n", "true"], ["unshare", "--net", "--map-root-user", "true"])
    return any(subprocess.run(c, capture_output=True).returncode == 0 for c in checks)  # noqa: S603


pytestmark = pytest.mark.skipif(not isolation_available(), reason="no way to make a network namespace here")


def run(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run([str(OFFLINE), *cmd], capture_output=True, text=True, timeout=60)  # noqa: S603


def test_nothing_outside_is_reachable():
    out = run("bash", "-c", "exec 3<>/dev/tcp/1.1.1.1/443")
    assert out.returncode != 0
    assert "Network is unreachable" in out.stderr


def test_the_command_still_runs_with_its_arguments_and_environment():
    out = run("bash", "-c", 'echo "$1 $TZ"', "_", "hello")
    assert out.returncode == 0, out.stderr
    assert out.stdout.split()[0] == "hello"
