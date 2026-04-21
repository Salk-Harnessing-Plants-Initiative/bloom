"""Unit tests for scripts/check-uv-locks.py.

Covers every control-flow branch of the pre-commit lockfile drift check:

- Test A: `uv` not on PATH  -> main() returns 127
- Test B: service dir missing pyproject.toml -> logged and skipped
- Test C: `uv lock --check` returns non-zero -> drift detected, returns 1
- Test D: all services clean -> returns 0
- Test E: subprocess.run raises TimeoutExpired -> caught, recorded, processing continues
- Test F: subprocess.run raises FileNotFoundError (uv disappears mid-run)
          -> caught, recorded, processing continues
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


# ---------- helpers ----------

SERVICES = ("langchain", "bloommcp", "services/video-worker")


def _fake_repo(tmp_path: Path, services_with_pyproject: tuple[str, ...] = SERVICES) -> Path:
    """Create a tmp repo root containing pyproject.toml stubs for the given services."""
    for service in services_with_pyproject:
        service_dir = tmp_path / service
        service_dir.mkdir(parents=True, exist_ok=True)
        (service_dir / "pyproject.toml").write_text("[project]\nname='stub'\n")
    return tmp_path


class _FakeCompleted:
    def __init__(self, returncode: int) -> None:
        self.returncode = returncode


# ---------- Test A ----------

def test_uv_missing_returns_127(check_uv_locks_module, monkeypatch, capsys):
    """When shutil.which('uv') returns None, main() should exit 127 with install hint."""
    monkeypatch.setattr(check_uv_locks_module.shutil, "which", lambda _name: None)

    rc = check_uv_locks_module.main()

    assert rc == 127
    err = capsys.readouterr().err
    assert "uv" in err
    assert "install" in err.lower()
    assert "https://docs.astral.sh/uv" in err


# ---------- Test B ----------

def test_missing_pyproject_skips(check_uv_locks_module, monkeypatch, tmp_path, capsys):
    """A service dir without pyproject.toml should be skipped, not cause failure."""
    # Only langchain has a pyproject; bloommcp and video-worker are absent.
    _fake_repo(tmp_path, services_with_pyproject=("langchain",))

    monkeypatch.setattr(
        check_uv_locks_module.subprocess,
        "run",
        lambda *a, **kw: _FakeCompleted(0),
    )

    rc = check_uv_locks_module.check_services(tmp_path)

    assert rc == 0
    out = capsys.readouterr().out
    assert "skip" in out
    assert "bloommcp" in out  # skip message mentions bloommcp
    assert "services/video-worker" in out  # and video-worker


# ---------- Test C ----------

def test_drift_detected_returns_1(check_uv_locks_module, monkeypatch, tmp_path, capsys):
    """If uv lock --check returns non-zero for a service, that service is flagged."""
    _fake_repo(tmp_path)

    def fake_run(cmd, cwd, **_kw):
        # Drift only in bloommcp
        if cwd.name == "bloommcp":
            return _FakeCompleted(1)
        return _FakeCompleted(0)

    monkeypatch.setattr(check_uv_locks_module.subprocess, "run", fake_run)

    rc = check_uv_locks_module.check_services(tmp_path)

    assert rc == 1
    err = capsys.readouterr().err
    assert "bloommcp" in err
    assert "drift" in err.lower()


# ---------- Test D ----------

def test_clean_pass_returns_0(check_uv_locks_module, monkeypatch, tmp_path):
    """All services clean -> returns 0."""
    _fake_repo(tmp_path)

    monkeypatch.setattr(
        check_uv_locks_module.subprocess,
        "run",
        lambda *a, **kw: _FakeCompleted(0),
    )

    rc = check_uv_locks_module.check_services(tmp_path)

    assert rc == 0


# ---------- Test E ----------

def test_subprocess_timeout_is_caught_and_recorded(
    check_uv_locks_module, monkeypatch, tmp_path, capsys
):
    """TimeoutExpired from the FIRST service must be caught; subsequent services still run."""
    _fake_repo(tmp_path)
    calls: list[str] = []

    def fake_run(cmd, cwd, **kw):
        calls.append(cwd.name)
        if cwd.name == "langchain":
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=120)
        return _FakeCompleted(0)

    monkeypatch.setattr(check_uv_locks_module.subprocess, "run", fake_run)

    rc = check_uv_locks_module.check_services(tmp_path)

    # Return code reflects a failure (timeout is treated as drift/failure).
    assert rc == 1
    # All three services were attempted (one stuck service did not block the rest).
    assert calls == ["langchain", "bloommcp", "video-worker"]
    err = capsys.readouterr().err
    assert "langchain" in err
    assert "timeout" in err.lower() or "timed out" in err.lower()


# ---------- Test F ----------

def test_subprocess_filenotfound_is_caught_and_recorded(
    check_uv_locks_module, monkeypatch, tmp_path, capsys
):
    """FileNotFoundError (uv vanishes between probe and exec) must be caught, not propagated."""
    _fake_repo(tmp_path)
    calls: list[str] = []

    def fake_run(cmd, cwd, **kw):
        calls.append(cwd.name)
        if cwd.name == "langchain":
            raise FileNotFoundError("uv: No such file or directory")
        return _FakeCompleted(0)

    monkeypatch.setattr(check_uv_locks_module.subprocess, "run", fake_run)

    rc = check_uv_locks_module.check_services(tmp_path)

    assert rc == 1
    # Processing continued past the FileNotFoundError to the other services.
    assert calls == ["langchain", "bloommcp", "video-worker"]
    err = capsys.readouterr().err
    assert "langchain" in err
    # Install-hint URL surfaces so the developer knows what to do.
    assert "https://docs.astral.sh/uv" in err


# ---------- Test G (real subprocess) ----------

@pytest.mark.skipif(shutil.which("uv") is None, reason="real uv binary not on PATH")
def test_real_subprocess_against_clean_tmp_repo(
    check_uv_locks_module, tmp_path, capsys
):
    """Guards the `subprocess.run(["uv", "lock", "--check"], ...)` contract.

    The other tests mock subprocess.run, so a typo like
    `["uv", "sync", "--check"]` (non-existent subcommand) would pass all of
    them. This test invokes a real uv and asserts check_services() agrees
    with the real binary on a known-clean repo.
    """
    # Build a minimal service directory with a valid pyproject.toml + lock.
    service_dir = tmp_path / "langchain"
    service_dir.mkdir()
    (service_dir / "pyproject.toml").write_text(
        '[project]\n'
        'name = "tmp-clean"\n'
        'version = "0.0.0"\n'
        'requires-python = ">=3.11"\n'
        'dependencies = []\n'
    )
    # Generate a real lockfile so `uv lock --check` has something to validate.
    lock_result = subprocess.run(
        ["uv", "lock"], cwd=service_dir, capture_output=True, text=True, timeout=60,
    )
    assert lock_result.returncode == 0, (
        f"uv lock failed during test setup: {lock_result.stderr}"
    )
    assert (service_dir / "uv.lock").exists()

    # Only langchain exists under tmp_path; bloommcp + video-worker are skipped.
    rc = check_uv_locks_module.check_services(tmp_path)

    assert rc == 0, "check_services should return 0 for a clean tmp repo"
    captured = capsys.readouterr()
    # The per-service "check ..." progress line proves the real subprocess ran.
    assert "check langchain" in captured.out
    # The two missing services should surface as skips, not failures.
    assert "skip  bloommcp" in captured.out
    assert "skip  services/video-worker" in captured.out
