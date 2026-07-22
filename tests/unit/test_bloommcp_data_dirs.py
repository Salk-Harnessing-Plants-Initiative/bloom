"""`scripts/ensure_bloommcp_data_dirs.sh` — bloommcp data-dir writability preflight.

Drives the script via subprocess (mirrors tests/unit/test_doctor.py's pattern),
scoped to a `tmp_path` root via the `BLOOMMCP_DATA_ROOT` testability override so
it never touches the real `bloommcp/data/`.

The "pre-existing directory owned by a genuinely different user (e.g. root,
from Docker's default create-as-root behavior)" case itself is NOT exercised
here — simulating a different owner requires root/setuid privilege this
hermetic suite does not have and should not assume. That path is validated by
a live Docker Compose reproduction instead (see openspec/changes/fix-bloommcp-
dev-data-dir-permissions/tasks.md). But the script's *handling* of a chmod
failure — abort, name the directory, non-zero exit — does not itself require a
real different owner, and is exercised hermetically here via a stubbed `chmod`
on `PATH` (`test_chmod_failure_on_existing_directory_aborts_with_actionable_
message`); this suite otherwise covers everything reachable as the test's own
user: fresh creation, idempotency, re-chmod of an existing directory (leaf and
root), and the mkdir failure path.
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


def test_unwritable_parent_aborts_with_actionable_message(tmp_path):
    """mkdir failing to create $ROOT itself (e.g. an unwritable parent
    directory) fails loudly and names the directory — it must never silently
    continue into the bug it exists to prevent.

    This simulates the unwritability one level up, at $ROOT's own parent
    (which the script never touches) — not at $ROOT itself. An owner-owned
    $ROOT, however restrictive its own mode, is now self-healed via `chmod`
    regardless of that mode (chmod is ownership-gated, not mode-gated) — see
    test_root_with_wrong_mode_is_rechmoded; it can no longer be used to
    simulate an unfixable state."""
    parent = tmp_path / "unwritable"
    parent.mkdir()
    parent.chmod(0o555)  # no write bit — mkdir of $ROOT itself must fail
    root = parent / "data"
    try:
        r = _run(root)
        assert r.returncode != 0
        assert "SLEAP_OUT_CSV" in r.stderr
    finally:
        parent.chmod(0o755)  # let pytest's tmp_path cleanup remove it


def test_root_with_wrong_mode_is_rechmoded(tmp_path):
    """A pre-existing $ROOT the caller owns, but with a stale narrow mode (e.g.
    0700 — not Docker's root-owned case, just a restrictive mode on $ROOT
    itself) previously went untouched even though every leaf under it got
    chmod'd to 0777: the container's non-root user still couldn't traverse
    $ROOT to reach any leaf. $ROOT itself must be corrected too."""
    root = tmp_path / "data"
    root.mkdir()
    root.chmod(0o700)
    r = _run(root)
    assert r.returncode == 0, f"stderr:\n{r.stderr}"
    assert _mode(root) == 0o777
    for name in DIR_NAMES:
        assert _mode(root / name) == 0o777


def test_chmod_failure_on_existing_directory_aborts_with_actionable_message(
    tmp_path,
):
    """The real-world failure mode #472 fixes is Docker having *already*
    created a directory as root before this script ever runs — so `mkdir -p`
    is a no-op (the directory exists) and `chmod` itself is what fails with
    EPERM. That can't be reproduced by an ordinary (non-root) test user
    actually owning the directory, so simulate the chmod failure with a stub
    `chmod` prepended to PATH instead — no elevated privilege required."""
    root = tmp_path / "data"
    root.mkdir()
    for name in DIR_NAMES:
        (root / name).mkdir()  # pre-exists — skips the mkdir branch entirely

    stub_bin = tmp_path / "stub_bin"
    stub_bin.mkdir()
    stub_chmod = stub_bin / "chmod"
    stub_chmod.write_text("#!/bin/sh\nexit 1\n")
    stub_chmod.chmod(0o755)

    r = subprocess.run(
        [SH, str(SCRIPT)],
        env={
            "BLOOMMCP_DATA_ROOT": str(root),
            "PATH": f"{stub_bin}:/usr/bin:/bin",
        },
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert "SLEAP_OUT_CSV" in r.stderr
