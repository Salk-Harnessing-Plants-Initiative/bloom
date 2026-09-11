"""Shape guard for the ER-diagram drift step in compose-health-check.

The step redraws _WIKI/SUPABASE/erd.md with tbls from the freshly migrated database and fails
when the committed file differs, uploading the redrawn file for the author to commit. It
ships in warning mode. The database password reaches tbls through the environment only.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
PR_CHECKS = REPO_ROOT / ".github" / "workflows" / "pr-checks.yml"
MAKEFILE = REPO_ROOT / "Makefile"
JOB = "compose-health-check"
ERD = "_WIKI/SUPABASE/erd.md"


def _job() -> dict:
    return yaml.safe_load(PR_CHECKS.read_text(encoding="utf-8"))["jobs"][JOB]


def _step_index(steps: list[dict], needle: str) -> int:
    matches = [i for i, s in enumerate(steps) if needle in str(s.get("run", "")) or needle == s.get("name")]
    assert len(matches) == 1, f"expected one step matching {needle!r}, found {len(matches)}"
    return matches[0]


def _drift_step() -> tuple[list[dict], int]:
    steps = _job()["steps"]
    return steps, _step_index(steps, "schema_erd.py wrap")


def test_drift_step_runs_after_the_grants_step():
    steps, drift = _drift_step()
    grants = _step_index(steps, "Apply bloom_* schema-USAGE grants")
    assert drift > grants


def test_drift_step_uses_the_pinned_image():
    steps, drift = _drift_step()
    assert "$TBLS_IMAGE" in steps[drift]["run"] or "${TBLS_IMAGE}" in steps[drift]["run"]
    assert "@sha256:" in _job()["env"]["TBLS_IMAGE"]


def test_password_reaches_tbls_through_the_environment_only():
    steps, drift = _drift_step()
    run = steps[drift]["run"]
    assert "-e TBLS_DSN" in run
    assert "--dsn" not in run
    assert not re.search(r"-e TBLS_DSN=", run), "pass the variable by name so its value stays out of argv"


def test_drift_step_checks_the_committed_file():
    steps, drift = _drift_step()
    assert f"git status --porcelain -- {ERD}" in steps[drift]["run"]


def test_drift_step_is_in_warning_mode():
    steps, drift = _drift_step()
    assert steps[drift].get("continue-on-error") is True


def test_redrawn_file_is_uploaded_when_the_check_fails():
    steps, drift = _drift_step()
    step_id = steps[drift].get("id")
    assert step_id, "the drift step needs an id so the upload can key on its outcome"
    uploads = [
        s for s in steps[drift + 1 :]
        if str(s.get("uses", "")).startswith("actions/upload-artifact")
        and ERD in str(s.get("with", {}).get("path", ""))
    ]
    assert len(uploads) == 1, "expected one upload of the redrawn erd.md"
    assert f"steps.{step_id}.outcome == 'failure'" in str(uploads[0].get("if", ""))
    assert uploads[0]["with"]["name"] == "erd"
    assert "erd artifact" in steps[drift]["run"]


def test_makefile_and_workflow_pin_the_same_image():
    pinned = re.search(r"^TBLS_IMAGE\s*\?=\s*(\S+)", MAKEFILE.read_text(encoding="utf-8"), re.M)
    assert pinned, "Makefile must define TBLS_IMAGE"
    assert pinned.group(1) == _job()["env"]["TBLS_IMAGE"]
