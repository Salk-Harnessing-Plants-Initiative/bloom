"""PR #635 round-3 review: behavioral tests for scripts/wait_for_kong_healthy.sh.

Extracted from deploy.yml's four previously-inline copies of the same
health-poll loop (forward-path restart + rollback restart, each for prod and
staging) — none of which had execution-level test coverage, only
text/string matching on the YAML (an asymmetry with
check_kong_restart_delta.sh, which was specifically extracted for
testability). Mirrors test_check_kong_restart_delta_script.py's technique of
invoking the real script via `subprocess` against a stubbed `docker` on
`PATH`, rather than reimplementing the polling logic in Python.

`docker` is stubbed to return a scripted sequence of healthcheck statuses
across successive `inspect` calls (one status consumed per call), so tests
can assert both the "becomes healthy after N polls" and "never becomes
healthy within the timeout" paths without any real sleeping beyond the
script's own (short, test-configured) poll interval.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "wait_for_kong_healthy.sh"

# See test_check_kong_restart_delta_script.py's identical helper for why
# this is needed: `bash` can resolve to the WSL launcher shim rather than a
# real POSIX shell on some Windows dev machines, depending on which
# process's PATH is being searched.
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

# Each `docker inspect` call pops the next status off a queue file (one
# status per line); once exhausted, it keeps returning the last entry — this
# lets a test express "starting, starting, then healthy" or an
# all-"unhealthy" sequence that never resolves within a short timeout.
FAKE_DOCKER = """\
#!/usr/bin/env bash
set -euo pipefail
if [ "$1" = "inspect" ]; then
  # Validate the script actually asks for State.Health.Status on the right
  # container, not just that *some* inspect call happened.
  if [ "${2-}" != "--format={{.State.Health.Status}}" ]; then
    echo "fake docker: unexpected inspect format arg: ${2-<missing>}" >&2
    exit 97
  fi
  if [ "${3-}" != "${FAKE_CONTAINER_ID-fake-cid}" ]; then
    echo "fake docker: unexpected inspect container id: ${3-<missing>}" >&2
    exit 97
  fi
  # Simulates the container disappearing mid-poll (e.g. removed/recreated
  # between calls) — the real docker inspect would exit non-zero with
  # nothing useful on stdout in that case.
  if [ -n "${FAKE_INSPECT_FAILS-}" ]; then
    echo "fake docker: simulated inspect failure" >&2
    exit 1
  fi
  queue="$FAKE_STATUS_QUEUE"
  next="$(head -n 1 "$queue")"
  # Pop the consumed line, unless it's the last one — repeat the last
  # status forever so a short queue still works past its own length.
  if [ "$(wc -l < "$queue")" -gt 1 ]; then
    tail -n +2 "$queue" > "$queue.tmp" && mv "$queue.tmp" "$queue"
  fi
  printf '%s' "$next"
  exit 0
fi
echo "unexpected docker invocation: $*" >&2
exit 99
"""


def _install_fake_docker(tmp_path: Path, statuses: list[str]) -> Path:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    docker_stub = fake_bin / "docker"
    docker_stub.write_text(FAKE_DOCKER)
    docker_stub.chmod(docker_stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    queue = tmp_path / "status_queue.txt"
    queue.write_text("\n".join(statuses) + "\n")
    return fake_bin


def _run_script(
    tmp_path: Path,
    args: list[str],
    statuses: list[str],
    inspect_fails: bool = False,
) -> subprocess.CompletedProcess:
    fake_bin = _install_fake_docker(tmp_path, statuses)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "FAKE_STATUS_QUEUE": str(tmp_path / "status_queue.txt"),
        "FAKE_CONTAINER_ID": args[0] if args else "fake-cid",
    }
    if inspect_fails:
        env["FAKE_INSPECT_FAILS"] = "1"
    return subprocess.run(
        [BASH, str(SCRIPT), *args],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


def test_returns_immediately_once_healthy(tmp_path):
    result = _run_script(tmp_path, ["fake-cid", "30", "1"], statuses=["healthy"])
    assert result.returncode == 0
    assert result.stdout.strip() == "healthy"


def test_polls_through_starting_before_becoming_healthy(tmp_path):
    result = _run_script(
        tmp_path, ["fake-cid", "30", "1"], statuses=["starting", "starting", "healthy"]
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "healthy"


def test_timeout_elapses_without_healthy_prints_last_status_and_exits_1(tmp_path):
    result = _run_script(tmp_path, ["fake-cid", "2", "1"], statuses=["unhealthy"])
    assert result.returncode == 1
    assert result.stdout.strip() == "unhealthy"


def test_timeout_with_container_stuck_starting_exits_1(tmp_path):
    result = _run_script(tmp_path, ["fake-cid", "2", "1"], statuses=["starting"])
    assert result.returncode == 1
    assert result.stdout.strip() == "starting"


def test_defaults_to_120s_timeout_and_3s_interval_when_omitted(tmp_path):
    # Regression guard: a future edit that drops the defaults (or changes
    # them silently) shouldn't pass unnoticed just because deploy.yml always
    # passes them explicitly today.
    script_text = SCRIPT.read_text(encoding="utf-8")
    assert 'timeout_seconds="${2:-120}"' in script_text
    assert 'poll_interval="${3:-3}"' in script_text


def test_missing_container_id_is_a_usage_error(tmp_path):
    result = _run_script(tmp_path, [], statuses=["healthy"])
    assert result.returncode == 2
    assert "Usage:" in result.stderr


@pytest.mark.parametrize("bad_timeout", ["not-a-number", "-1"])
def test_non_numeric_timeout_is_a_usage_error(tmp_path, bad_timeout):
    # Note: an empty string ("") is deliberately not tested as a failure
    # case here — bash's `${2:-120}` treats an empty positional argument the
    # same as an unset one, so it falls back to the 120s default rather than
    # reaching validation. That's an accepted, harmless quirk of `:-`, not a
    # gap this script needs to close.
    result = _run_script(tmp_path, ["fake-cid", bad_timeout, "1"], statuses=["healthy"])
    assert result.returncode == 2
    assert "::error::" in result.stderr


@pytest.mark.parametrize("bad_interval", ["not-a-number", "-1", "0"])
def test_non_positive_poll_interval_is_a_usage_error(tmp_path, bad_interval):
    """poll_interval=0 would spin the while loop forever without ever
    advancing `elapsed` — must be rejected, not just non-numeric values."""
    result = _run_script(tmp_path, ["fake-cid", "30", bad_interval], statuses=["healthy"])
    assert result.returncode == 2
    assert "::error::" in result.stderr


def test_container_disappearing_mid_poll_falls_back_to_unknown(tmp_path):
    """Documented behavior (docker inspect failing -> `unknown`, via the
    script's `2>/dev/null || echo unknown` fallback) had zero test coverage
    before this — every existing test only varied the *printed* status,
    never made `docker inspect` itself fail."""
    result = _run_script(tmp_path, ["fake-cid", "2", "1"], statuses=["healthy"], inspect_fails=True)
    assert result.returncode == 1
    assert result.stdout.strip() == "unknown"


@pytest.mark.parametrize("perm_bit", [stat.S_IXUSR])
def test_script_is_executable(perm_bit):
    mode = SCRIPT.stat().st_mode
    assert mode & perm_bit, "scripts/wait_for_kong_healthy.sh must be chmod +x"
