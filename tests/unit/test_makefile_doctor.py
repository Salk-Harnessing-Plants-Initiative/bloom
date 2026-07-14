"""`make dev-up` must run the environment doctor as a preflight, BEFORE bring-up.

Wiring the doctor after `docker compose up` would defeat the whole point (the
stack would already be coming up). This pins: a `doctor` target exists; the
`dev-up` recipe invokes `scripts/doctor.sh` and does so *before* the
`docker compose ... up` line; and a hard doctor error aborts `dev-up` before it
touches the frontend/compose steps.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"


def _recipe(target: str) -> str:
    lines = MAKEFILE.read_text(encoding="utf-8").splitlines()
    out, capturing = [], False
    for line in lines:
        if line.startswith(f"{target}:"):
            capturing = True
            continue
        if capturing:
            if line and line[0] not in (" ", "\t"):
                break
            out.append(line)
    return "\n".join(out)


def _real_targets() -> set[str]:
    import re

    return {
        m.group(1)
        for line in MAKEFILE.read_text(encoding="utf-8").splitlines()
        if (m := re.match(r"^([a-zA-Z0-9_][a-zA-Z0-9_-]*):", line))
    }


def test_doctor_target_exists():
    assert "doctor" in _real_targets(), "a `make doctor` target must exist"


def test_doctor_target_runs_the_script():
    assert "scripts/doctor.sh" in _recipe(
        "doctor"
    ), "`make doctor` must run scripts/doctor.sh"


def test_dev_up_runs_doctor_before_compose():
    recipe = _recipe("dev-up")
    assert "scripts/doctor.sh" in recipe, "dev-up must run the doctor preflight"
    assert "docker compose" in recipe, "dev-up must bring the stack up"
    assert recipe.index("scripts/doctor.sh") < recipe.index("docker compose"), (
        "the doctor preflight must run BEFORE `docker compose up`, or the stack "
        "is already coming up when the environment is checked"
    )


@pytest.mark.skipif(shutil.which("make") is None, reason="make not on PATH")
def test_dev_up_aborts_before_bring_up_on_doctor_error():
    """A hard doctor error (repo under /mnt/) must abort `dev-up` before the
    frontend/compose steps run — nothing is brought up."""
    env = {**os.environ, "DOCTOR_WSL": "1", "DOCTOR_REPO_PATH": "/mnt/c/repos/bloom"}
    result = subprocess.run(
        ["make", "dev-up"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0, "dev-up must fail when the doctor errors"
    combined = result.stdout + result.stderr
    assert "Checking frontend dependencies" not in combined, (
        "dev-up reached the frontend step despite a doctor error — the preflight "
        "did not abort bring-up"
    )
