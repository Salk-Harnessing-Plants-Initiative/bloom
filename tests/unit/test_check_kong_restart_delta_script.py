"""Issue #634: behavioral tests for scripts/check_kong_restart_delta.sh.

Single source of truth for the Kong crash-loop delta/threshold decision,
called identically by both deploy.yml jobs (see
openspec/changes/fix-kong-reload-on-deploy/design.md Decision 3). Mirrors
tests/unit/test_env_defaults.py's technique of invoking the real script via
subprocess against controlled fixtures, rather than reimplementing its
logic in Python — the workflow and the tests can never drift.

`docker` is stubbed as a fake executable placed first on PATH: it
dispatches on its first argument (`inspect` -> prints a controlled
RestartCount from an env var; `compose` -> matches `ps -q kong` / `logs
--tail=100 kong` / `stop kong` against its remaining args, returning a
controlled container id and recording each call to a log file).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "check_kong_restart_delta.sh"

# On some Windows dev machines, `bash` resolves to the WSL launcher shim
# (C:\Windows\System32\bash.exe) rather than Git for Windows' real bash,
# depending on which process's PATH is being searched — a WSL bash can't
# resolve a Windows-style path like the one Path(SCRIPT) produces, nor does
# it reliably inherit env vars passed via subprocess `env=` without WSLENV
# configuration. Prefer a known-real bash when present; on Linux CI this
# constant is simply never used since `shutil.which("bash")` already finds
# the genuine one.
_GIT_BASH_CANDIDATES = [
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
]


def _bash_executable() -> str:
    for candidate in _GIT_BASH_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return shutil.which("bash") or "bash"


BASH = _bash_executable()

FAKE_DOCKER = """\
#!/usr/bin/env bash
set -euo pipefail
log="$FAKE_DOCKER_CALL_LOG"
if [ "$1" = "inspect" ]; then
  echo "inspect $*" >> "$log"
  printf '%s' "${FAKE_RESTART_COUNT-}"
  exit 0
fi
if [ "$1" = "compose" ]; then
  shift
  echo "compose $*" >> "$log"
  case " $* " in
    *" ps "*"-q"*"kong"*)
      printf '%s' "${FAKE_CONTAINER_ID-fake-cid}"
      ;;
    *" logs "*)
      echo "LOGS_CALLED" >> "$log"
      ;;
    *" stop "*)
      echo "STOP_CALLED" >> "$log"
      ;;
  esac
  exit 0
fi
echo "unexpected docker invocation: $*" >&2
exit 99
"""

COMPOSE_ARGS = ["docker", "compose", "-f", "docker-compose.prod.yml", "--env-file", ".env.prod"]


def _install_fake_docker(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    docker_stub = fake_bin / "docker"
    docker_stub.write_text(FAKE_DOCKER)
    docker_stub.chmod(docker_stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return fake_bin


def _run_script(
    tmp_path: Path,
    args: list[str],
    restart_count: str | None = "",
    container_id: str | None = "fake-cid",
) -> subprocess.CompletedProcess:
    fake_bin = _install_fake_docker(tmp_path)
    call_log = tmp_path / "calls.log"
    call_log.write_text("")
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_DOCKER_CALL_LOG": str(call_log),
    }
    if restart_count is not None:
        env["FAKE_RESTART_COUNT"] = restart_count
    if container_id is not None:
        env["FAKE_CONTAINER_ID"] = container_id
    result = subprocess.run(
        [BASH, str(SCRIPT), *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    result.call_log = call_log.read_text() if call_log.exists() else ""
    return result


@pytest.mark.parametrize(
    "before,restart_count,delta_label",
    [
        ("5", "6", "delta 1 (our own restart)"),
        ("5", "7", "delta 2 (exactly at threshold)"),
    ],
)
def test_delta_within_or_at_threshold_passes_without_stopping_kong(
    tmp_path, before, restart_count, delta_label
):
    result = _run_script(tmp_path, [before, "2", "--", *COMPOSE_ARGS], restart_count=restart_count)
    assert result.returncode == 0, f"{delta_label}: {result.stderr}"
    assert "LOGS_CALLED" not in result.call_log
    assert "STOP_CALLED" not in result.call_log


@pytest.mark.parametrize(
    "before,restart_count,delta_label",
    [
        ("5", "8", "delta 3 (threshold + 1, the real boundary)"),
        ("5", "9", "delta 4 (well over threshold)"),
    ],
)
def test_delta_over_threshold_stops_kong_and_fails(tmp_path, before, restart_count, delta_label):
    result = _run_script(tmp_path, [before, "2", "--", *COMPOSE_ARGS], restart_count=restart_count)
    assert result.returncode == 1, f"{delta_label}: expected exit 1, got {result.returncode}"
    assert "LOGS_CALLED" in result.call_log, delta_label
    assert "STOP_CALLED" in result.call_log, delta_label
    assert "::error::" in result.stderr


def test_missing_container_fails_cleanly_without_inspecting(tmp_path):
    result = _run_script(tmp_path, ["5", "2", "--", *COMPOSE_ARGS], container_id="")
    assert result.returncode == 1
    assert "::error::" in result.stderr
    assert "inspect" not in result.call_log, (
        "must not attempt docker inspect when the container doesn't exist"
    )


def test_non_numeric_restart_count_fails_cleanly(tmp_path):
    result = _run_script(tmp_path, ["5", "2", "--", *COMPOSE_ARGS], restart_count="not-a-number")
    # The key assertion is returncode == 1, not 0 — a silent coercion to 0
    # would compute delta = 0 - 5 = -5 <= threshold and exit 0, masking the
    # real problem (a docker inspect call that isn't returning what's
    # expected). This scenario isn't the same as the crash-loop path, so it
    # isn't required to dump logs/stop kong — just to fail loudly rather
    # than silently accepting a nonsensical reading as "fine".
    assert result.returncode == 1
    assert "::error::" in result.stderr


def test_empty_restart_count_fails_cleanly(tmp_path):
    result = _run_script(tmp_path, ["5", "2", "--", *COMPOSE_ARGS], restart_count="")
    assert result.returncode == 1
    assert "::error::" in result.stderr


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["5"],
        ["5", "2"],
        ["5", "2", "docker", "compose"],  # missing the `--` separator
    ],
)
def test_usage_error_exits_2(tmp_path, args):
    result = _run_script(tmp_path, args, restart_count="5")
    assert result.returncode == 2, f"args={args}: expected exit 2, got {result.returncode}"


def test_delta_zero_or_negative_passes(tmp_path):
    """RestartCount can't legitimately go backward, but the arithmetic
    shouldn't blow up or misfire if it somehow reads the same or a lower
    value (e.g. a container recreation resetting the counter)."""
    result = _run_script(tmp_path, ["5", "2", "--", *COMPOSE_ARGS], restart_count="5")
    assert result.returncode == 0
    assert "LOGS_CALLED" not in result.call_log
