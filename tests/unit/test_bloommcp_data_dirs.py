"""`scripts/ensure_bloommcp_data_dirs.sh` — bloommcp data-dir writability preflight.

Drives the script via subprocess (mirrors tests/unit/test_doctor.py's pattern),
scoped to a `tmp_path` root via the `BLOOMMCP_DATA_ROOT` testability override so
it never touches the real `bloommcp/data/`.

The "pre-existing directory owned by a different user (e.g. root, from Docker's
default create-as-root behavior) cannot be chmod'd" path is NOT exercised here —
simulating a different owner requires root/setuid privilege this hermetic suite
does not have and should not assume. That path is validated by a live Docker
Compose reproduction instead (see openspec/changes/fix-bloommcp-dev-data-dir-
permissions/tasks.md); this suite covers everything reachable as the test's own
user: fresh creation, idempotency, re-chmod of an existing directory, and the
mkdir failure path.
"""

from __future__ import annotations

import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "ensure_bloommcp_data_dirs.sh"
SH = shutil.which("sh")
DIR_NAMES = ("SLEAP_OUT_CSV", "PLOTS_DIR", "ANALYSIS_OUTPUT")

pytestmark = pytest.mark.skipif(
    SH is None, reason="POSIX sh not available (run in WSL on Windows)"
)


def _run(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [SH, str(SCRIPT)],
        env={"BLOOMMCP_DATA_ROOT": str(root), "PATH": "/usr/bin:/bin"},
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_creates_missing_directories_writable(tmp_path):
    root = tmp_path / "data"
    r = _run(root)
    assert r.returncode == 0, f"stderr:\n{r.stderr}"
    for name in DIR_NAMES:
        d = root / name
        assert d.is_dir()
        assert _mode(d) == 0o777


def test_idempotent_on_a_second_run(tmp_path):
    root = tmp_path / "data"
    assert _run(root).returncode == 0
    r2 = _run(root)
    assert r2.returncode == 0, f"stderr:\n{r2.stderr}"
    for name in DIR_NAMES:
        assert _mode(root / name) == 0o777


def test_existing_directory_with_wrong_mode_is_rechmoded(tmp_path):
    """An existing directory the caller owns, but with a stale narrower mode
    (e.g. from before this fix existed), is corrected — not left as-is."""
    root = tmp_path / "data"
    root.mkdir()
    for name in DIR_NAMES:
        d = root / name
        d.mkdir()
        d.chmod(0o700)
    r = _run(root)
    assert r.returncode == 0, f"stderr:\n{r.stderr}"
    for name in DIR_NAMES:
        assert _mode(root / name) == 0o777


def test_unwritable_root_aborts_with_actionable_message(tmp_path):
    """mkdir failing (e.g. an unwritable parent) fails loudly and names the
    directory — it must never silently continue into the bug it exists to
    prevent."""
    root = tmp_path / "data"
    root.mkdir()
    root.chmod(0o555)  # no write bit — mkdir of a child dir must fail
    try:
        r = _run(root)
        assert r.returncode != 0
        assert "SLEAP_OUT_CSV" in r.stderr
    finally:
        root.chmod(0o755)  # let pytest's tmp_path cleanup remove it
