"""`scripts/migrate_bloommcp_legacy_traits_dir.sh` — issue #477's deploy-host migration of a
pre-existing bloommcp/data/SLEAP_OUT_CSV directory to bloommcp/data/TRAITS_DIR.

Drives the script via subprocess (mirrors tests/unit/test_bloommcp_data_dirs.py's pattern),
scoped to a `tmp_path` root via the `BLOOMMCP_DATA_ROOT` testability override so it never
touches the real `bloommcp/data/`.

Covers every branch a PR review flagged as untested: the ambiguous both-exist state (§1), and
the parent-directory-unwritable failure mode (§2) — a rename needs write permission on the
directory *containing* the entry being renamed, not the entry itself, so a root-owned
`bloommcp/data` parent breaks the migration even if the leaf itself is fine.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "migrate_bloommcp_legacy_traits_dir.sh"
SH = shutil.which("sh")

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


def test_fresh_host_is_a_noop(tmp_path):
    """Neither directory exists yet — nothing to migrate, exit 0."""
    root = tmp_path / "data"
    r = _run(root)
    assert r.returncode == 0, f"stderr:\n{r.stderr}"
    assert "no migration needed" in r.stdout.lower()
    assert not (root / "SLEAP_OUT_CSV").exists()
    assert not (root / "TRAITS_DIR").exists()


def test_migrates_populated_legacy_directory(tmp_path):
    """The real case this script exists for: a populated legacy directory, no new one yet."""
    root = tmp_path / "data"
    old = root / "SLEAP_OUT_CSV"
    old.mkdir(parents=True)
    (old / "plant_traits.csv").write_text("a,b,c\n1,2,3\n")

    r = _run(root)

    assert r.returncode == 0, f"stderr:\n{r.stderr}"
    assert not old.exists(), "SLEAP_OUT_CSV must no longer exist after a successful migration"
    new = root / "TRAITS_DIR"
    assert new.is_dir()
    assert (new / "plant_traits.csv").read_text() == "a,b,c\n1,2,3\n", (
        "migration must preserve directory contents, not just create an empty TRAITS_DIR"
    )


def test_already_migrated_host_is_a_noop(tmp_path):
    """New directory already exists, old one doesn't — a prior deploy already migrated it."""
    root = tmp_path / "data"
    new = root / "TRAITS_DIR"
    new.mkdir(parents=True)
    (new / "plant_traits.csv").write_text("already here\n")

    r = _run(root)

    assert r.returncode == 0, f"stderr:\n{r.stderr}"
    assert "no migration needed" in r.stdout.lower()
    assert (new / "plant_traits.csv").read_text() == "already here\n"


def test_both_directories_existing_is_ambiguous_and_fails_loud(tmp_path):
    """Review finding: silently treating 'both exist' as 'already migrated' would leave real,
    un-migrated data in SLEAP_OUT_CSV behind with no warning — the exact silent-misconfiguration
    class of bug this whole change exists to fix. Must refuse and name both paths, not reuse the
    'already migrated or fresh host' message."""
    root = tmp_path / "data"
    old = root / "SLEAP_OUT_CSV"
    new = root / "TRAITS_DIR"
    old.mkdir(parents=True)
    (old / "real_unmigrated_data.csv").write_text("do not discard me\n")
    new.mkdir(parents=True)

    r = _run(root)

    assert r.returncode != 0, "must fail loudly, not silently no-op, when both directories exist"
    assert str(old) in r.stderr
    assert str(new) in r.stderr
    assert (old / "real_unmigrated_data.csv").exists(), (
        "the real data must be left in place, not silently discarded, on this ambiguous path"
    )
    # Must NOT reuse the reassuring "already migrated or fresh host" message for this case --
    # that message is factually false when SLEAP_OUT_CSV still holds un-migrated data.
    assert "already migrated" not in r.stdout.lower()


def test_unwritable_parent_aborts_with_actionable_message(tmp_path):
    """Review finding: a rename needs write permission on the CONTAINING directory, not the
    leaf being renamed. Simulates a root-owned bloommcp/data parent (the same mechanism that
    breaks the three leaf directories can equally apply to the parent itself) by making the
    parent read-only -- mkdir/write into it must fail, and so must the eventual mv."""
    parent = tmp_path / "unwritable"
    parent.mkdir()
    root = parent / "data"
    # Create $ROOT and the legacy leaf while still writable, matching a real host where the
    # directories exist but the deploy user's write bit was later revoked (or was never
    # granted) on $ROOT itself -- the leaf's own permissions are irrelevant to `mv`.
    old = root / "SLEAP_OUT_CSV"
    old.mkdir(parents=True)
    (old / "plant_traits.csv").write_text("data\n")
    root.chmod(0o555)  # no write bit on $ROOT -- mv cannot update its directory entries
    try:
        r = _run(root)
        assert r.returncode != 0
        assert str(root) in r.stderr
        assert "sudo chown" in r.stderr
        assert old.exists(), "the legacy directory must be left untouched when the parent is unwritable"
    finally:
        root.chmod(0o755)  # let pytest's tmp_path cleanup remove it
