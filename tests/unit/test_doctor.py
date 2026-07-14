"""`scripts/doctor.sh` — dev-environment preflight.

Drives the doctor via subprocess under a fully controlled `PATH` so every check
is deterministic on the Linux CI runner (the `python-audit` job has only
`uv`+`git`, not node/npm/supabase/docker/make). Skips where POSIX `sh` is
unavailable (native-Windows dev machines run this suite inside WSL).

Testability hooks used here (see the change's design.md): `DOCTOR_WSL`,
`DOCTOR_REPO_PATH`, `DOCTOR_MNT_PREFIX`, `DOCTOR_PORT`, `DOCTOR_SCAN_ROOT`,
`DOCTOR_SKIP`.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCTOR = REPO_ROOT / "scripts" / "doctor.sh"
SH = shutil.which("sh")
TOOLS = ("uv", "node", "npm", "supabase", "make", "docker")
# External commands the doctor invokes (must be reachable on the isolated PATH).
COREUTILS = ("dirname", "grep", "sed", "tr", "awk", "find", "head", "cat", "env")

pytestmark = pytest.mark.skipif(
    SH is None, reason="POSIX sh not available (run in WSL on Windows)"
)


def _make_bin(
    tmp_path, *, tools=TOOLS, supabase_version="2.92.1", include_net=False
) -> Path:
    """A controlled bin dir: coreutils symlinks + stub tool executables."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for cu in COREUTILS:
        real = shutil.which(cu)
        if real:
            (bin_dir / cu).symlink_to(real)
    if include_net:
        for net in ("ss", "nc"):
            real = shutil.which(net)
            if real:
                (bin_dir / net).symlink_to(real)
    for tool in tools:
        stub = bin_dir / tool
        if tool == "supabase":
            stub.write_text(f'#!/bin/sh\necho "{supabase_version}"\n')
        else:
            stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    return bin_dir


def _run(
    tmp_path, *, env=None, tools=TOOLS, supabase_version="2.92.1", include_net=False
):
    bin_dir = _make_bin(
        tmp_path,
        tools=tools,
        supabase_version=supabase_version,
        include_net=include_net,
    )
    full_env = {"PATH": str(bin_dir)}
    if env:
        full_env.update(env)
    return subprocess.run(
        [SH, str(DOCTOR)],
        env=full_env,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )


# --- Check 1: repo on the Windows filesystem is a hard error ---


def test_repo_on_mnt_is_error(tmp_path):
    r = _run(
        tmp_path, env={"DOCTOR_WSL": "1", "DOCTOR_REPO_PATH": "/mnt/c/repos/bloom"}
    )
    assert r.returncode != 0
    assert "/mnt/" in r.stderr
    assert "WSL2 Linux filesystem" in r.stderr


# --- Check 2: required tools + Windows-mount leak ---


@pytest.mark.parametrize("missing", TOOLS)
def test_missing_required_tool_is_error(tmp_path, missing):
    present = tuple(t for t in TOOLS if t != missing)
    r = _run(tmp_path, tools=present, env={"DOCTOR_WSL": "0"})
    assert r.returncode != 0, f"missing {missing} should be a hard error"
    assert missing in r.stderr


def test_all_tools_present_no_tool_error(tmp_path):
    r = _run(tmp_path, env={"DOCTOR_WSL": "0", "DOCTOR_SCAN_ROOT": str(tmp_path)})
    assert r.returncode == 0, f"clean env should exit 0; stderr:\n{r.stderr}"


def test_windows_mount_leak_is_warning(tmp_path):
    # Treat the stub bin dir itself as the "Windows mount": every tool resolves
    # under it, so the leak branch fires — but it is advisory (exit 0).
    bin_prefix = str(tmp_path / "bin") + "/"
    r = _run(
        tmp_path,
        env={
            "DOCTOR_WSL": "1",
            "DOCTOR_MNT_PREFIX": bin_prefix,
            "DOCTOR_SCAN_ROOT": str(tmp_path),
        },
    )
    assert r.returncode == 0, f"a leak alone must not fail; stderr:\n{r.stderr}"
    assert "leaking via /mnt" in r.stderr
    assert "node" in r.stderr


# --- Check 3: supabase version vs pinned .supabase-version ---


def test_supabase_version_mismatch_warns(tmp_path):
    r = _run(
        tmp_path,
        supabase_version="9.9.9",
        env={"DOCTOR_WSL": "0", "DOCTOR_SCAN_ROOT": str(tmp_path)},
    )
    assert r.returncode == 0
    assert "supabase CLI is 9.9.9" in r.stderr


def test_supabase_version_match_no_warn(tmp_path):
    pin = (REPO_ROOT / ".supabase-version").read_text().strip()
    r = _run(
        tmp_path,
        supabase_version=pin,
        env={"DOCTOR_WSL": "0", "DOCTOR_SCAN_ROOT": str(tmp_path)},
    )
    assert r.returncode == 0
    assert "supabase CLI is" not in r.stderr


# --- Check 4: configured host port already in use ---


def test_occupied_host_port_warns(tmp_path):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
        r = _run(
            tmp_path,
            include_net=True,
            env={
                "DOCTOR_WSL": "0",
                "DOCTOR_PORT": str(port),
                "DOCTOR_SCAN_ROOT": str(tmp_path),
            },
        )
    if shutil.which("ss") is None and shutil.which("nc") is None:
        pytest.skip("no ss/nc to probe ports")
    assert r.returncode == 0
    assert f"host port {port} is already in use" in r.stderr


def test_free_host_port_no_warn(tmp_path):
    # Obtain a port then release it; it is very likely free for the probe.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    r = _run(
        tmp_path,
        include_net=True,
        env={
            "DOCTOR_WSL": "0",
            "DOCTOR_PORT": str(port),
            "DOCTOR_SCAN_ROOT": str(tmp_path),
        },
    )
    assert r.returncode == 0
    assert "already in use" not in r.stderr


# --- Check 5: CRLF in bind-mounted init scripts (both globs) ---


def test_crlf_in_minio_init_warns(tmp_path):
    scan = tmp_path / "scan"
    (scan / "minio" / "init").mkdir(parents=True)
    (scan / "minio" / "init" / "create-buckets.sh").write_bytes(
        b"#!/bin/sh\r\nset -e\r\n"
    )
    r = _run(tmp_path, env={"DOCTOR_WSL": "0", "DOCTOR_SCAN_ROOT": str(scan)})
    assert r.returncode == 0
    assert "CRLF" in r.stderr and "create-buckets.sh" in r.stderr


def test_crlf_in_volumes_db_warns(tmp_path):
    scan = tmp_path / "scan"
    (scan / "volumes" / "db" / "init").mkdir(parents=True)
    (scan / "volumes" / "db" / "init" / "02-roles.sql").write_bytes(b"select 1;\r\n")
    r = _run(tmp_path, env={"DOCTOR_WSL": "0", "DOCTOR_SCAN_ROOT": str(scan)})
    assert r.returncode == 0
    assert "CRLF" in r.stderr and "02-roles.sql" in r.stderr


def test_lf_only_tree_no_crlf_warn(tmp_path):
    scan = tmp_path / "scan"
    (scan / "minio" / "init").mkdir(parents=True)
    (scan / "minio" / "init" / "create-buckets.sh").write_bytes(b"#!/bin/sh\nset -e\n")
    r = _run(tmp_path, env={"DOCTOR_WSL": "0", "DOCTOR_SCAN_ROOT": str(scan)})
    assert r.returncode == 0
    assert "CRLF" not in r.stderr


# --- Precedence + skip + self-guard ---


def test_error_takes_precedence_over_warnings(tmp_path):
    """The real /mnt/c case: an ERROR plus advisories must still exit non-zero
    and print both."""
    scan = tmp_path / "scan"
    (scan / "minio" / "init").mkdir(parents=True)
    (scan / "minio" / "init" / "create-buckets.sh").write_bytes(b"#!/bin/sh\r\n")
    r = _run(
        tmp_path,
        env={
            "DOCTOR_WSL": "1",
            "DOCTOR_REPO_PATH": "/mnt/c/repos/bloom",
            "DOCTOR_SCAN_ROOT": str(scan),
        },
    )
    assert r.returncode != 0, "a hard error must fail even with advisories present"
    assert "/mnt/" in r.stderr  # the error
    assert "CRLF" in r.stderr  # the advisory, still printed


def test_doctor_skip_short_circuits(tmp_path):
    # Even with a hard error condition, DOCTOR_SKIP=1 exits 0 without checking.
    r = _run(
        tmp_path,
        tools=(),  # no tools at all — would normally be many errors
        env={"DOCTOR_SKIP": "1", "DOCTOR_WSL": "1", "DOCTOR_REPO_PATH": "/mnt/c/x"},
    )
    assert r.returncode == 0
    assert "DOCTOR_SKIP" in r.stdout


def test_real_repo_init_scripts_are_lf(tmp_path):
    """Self-guard: scanning the real repo tree with all tools stubbed exits 0,
    proving the committed minio/init + volumes/db scripts are LF (a CRLF
    regression would fail here)."""
    r = _run(tmp_path, env={"DOCTOR_WSL": "0"})  # default DOCTOR_SCAN_ROOT = repo root
    assert (
        r.returncode == 0
    ), f"doctor should be clean on the real repo; stderr:\n{r.stderr}"
    assert "CRLF" not in r.stderr


def test_crlf_scan_prunes_volumes_db_data(tmp_path):
    """The CRLF scan must NOT descend into volumes/db/data/ — that's the live,
    gitignored Postgres cluster (binary files with 0x0D bytes). Scanning it would
    be slow and flag hundreds of false 'CRLF init script' warnings after the
    stack has run once. Only *.sh/*.sql init scripts are in scope."""
    scan = tmp_path / "scan"
    (scan / "volumes" / "db" / "data" / "base" / "1").mkdir(parents=True)
    # A binary Postgres-like file with a CR byte, under data/ — must be ignored.
    (scan / "volumes" / "db" / "data" / "base" / "1" / "1247").write_bytes(
        b"\x00\x01\r\x02binarypage\r\n"
    )
    # A legit LF init script alongside it — must not trigger anything.
    (scan / "volumes" / "db" / "init").mkdir(parents=True)
    (scan / "volumes" / "db" / "init" / "01-roles.sql").write_bytes(b"select 1;\n")
    r = _run(tmp_path, env={"DOCTOR_WSL": "0", "DOCTOR_SCAN_ROOT": str(scan)})
    assert r.returncode == 0
    assert "CRLF" not in r.stderr, f"data/ dir should be pruned; stderr:\n{r.stderr}"


def test_missing_pin_file_skips_version_check(tmp_path):
    """If the version pin file is absent, the supabase-version check is skipped
    (no warning) even when the CLI version differs."""
    r = _run(
        tmp_path,
        supabase_version="9.9.9",
        env={
            "DOCTOR_WSL": "0",
            "DOCTOR_SCAN_ROOT": str(tmp_path),
            "DOCTOR_PIN_FILE": str(tmp_path / "nonexistent-pin"),
        },
    )
    assert r.returncode == 0
    assert "supabase CLI is" not in r.stderr
