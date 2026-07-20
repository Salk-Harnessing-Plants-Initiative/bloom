"""`make dev-up` must provision bloommcp's data directories BEFORE compose brings
the container up, and that provisioning must run unconditionally — never gated
by `DOCTOR_SKIP` (issue #472).

CI runs `DOCTOR_SKIP=1 make dev-up` (see test_ci_dev_stack_smoke.py). If this
fix were folded into `scripts/doctor.sh` instead of wired as its own Makefile
prerequisite, CI would silently skip it forever and any CI check added to
verify the fix would fail for a reason invisible from its own code. This test
file pins that the fix lives outside `doctor.sh` and always runs.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
DOCTOR_SH = REPO_ROOT / "scripts" / "doctor.sh"


def _prereqs(target: str) -> list[str]:
    """The prerequisite list on a Makefile target's own `target: a b c` line —
    distinct from its recipe body (see test_makefile_doctor.py's `_recipe`,
    which captures the body, not this)."""
    for line in MAKEFILE.read_text(encoding="utf-8").splitlines():
        m = re.match(rf"^{re.escape(target)}:\s*(.*)$", line)
        if m:
            return m.group(1).split()
    raise AssertionError(f"no `{target}:` line found in Makefile")


def _real_targets() -> set[str]:
    return {
        m.group(1)
        for line in MAKEFILE.read_text(encoding="utf-8").splitlines()
        if (m := re.match(r"^([a-zA-Z0-9_][a-zA-Z0-9_-]*):", line))
    }


def test_ensure_bloommcp_data_dirs_target_exists():
    assert "ensure-bloommcp-data-dirs" in _real_targets()


def test_ensure_bloommcp_data_dirs_target_runs_the_script():
    body = "\n".join(
        line
        for line in MAKEFILE.read_text(encoding="utf-8").splitlines()
        if "ensure-bloommcp-data-dirs" in line or line.startswith("\t")
    )
    assert "scripts/ensure_bloommcp_data_dirs.sh" in body


def test_dev_up_depends_on_ensure_bloommcp_data_dirs():
    prereqs = _prereqs("dev-up")
    assert "ensure-bloommcp-data-dirs" in prereqs, (
        "`dev-up` must list `ensure-bloommcp-data-dirs` as a prerequisite "
        "(Make resolves prerequisites before the recipe body, unlike an inline "
        "recipe line which could accidentally land after `docker compose up`)"
    )


def test_data_dir_fix_is_not_folded_into_doctor_sh():
    """Regression guard: the fix must never live inside doctor.sh, or CI's
    DOCTOR_SKIP=1 would silently disable it."""
    doctor_text = DOCTOR_SH.read_text(encoding="utf-8")
    assert "ensure_bloommcp_data_dirs" not in doctor_text
    assert "PLOTS_DIR" not in doctor_text
    assert "BLOOMMCP_DATA_ROOT" not in doctor_text


def test_dev_up_aborts_before_bring_up_when_data_dirs_unwritable(tmp_path):
    """Behavioral counterpart to the structural checks above: an unwritable
    BLOOMMCP_DATA_ROOT aborts dev-up before the frontend/compose steps run.
    Uses an absolute path outside the repo (BLOOMMCP_DATA_ROOT), so nothing in
    the working tree is touched."""
    unwritable_parent = tmp_path / "unwritable"
    unwritable_parent.mkdir()
    unwritable_parent.chmod(0o555)
    try:
        env = {
            **os.environ,
            "BLOOMMCP_DATA_ROOT": str(unwritable_parent / "data"),
        }
        result = subprocess.run(
            ["make", "dev-up"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "Checking frontend dependencies" not in combined, (
            "dev-up reached the frontend step despite the data-dir preflight "
            "failing — it did not abort bring-up"
        )
    finally:
        unwritable_parent.chmod(0o755)
