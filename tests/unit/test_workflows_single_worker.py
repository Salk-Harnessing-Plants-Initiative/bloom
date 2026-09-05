"""The workflows service must run one uvicorn worker.

Video generation serialises per scan with a `threading.Lock` in
`services/workflows/video.py`. That lock is a module global, so it holds only within one
process: with two workers, two requests for the same scan land in different interpreters,
both read the recorded frame count before either uploads, and both write the same
unversioned `cyl-videos/{scan_id}.mp4`. The worse encode can land last and win.

The service ran `--workers 2` until this was pinned, which made the lock a no-op in
production while the code read as if the race were closed. Raising the count again is
only safe once the lock moves into the database.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"


def _workflows_command() -> list[str]:
    compose = yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))
    return compose["services"]["workflows"]["command"]


def test_workflows_runs_a_single_uvicorn_worker():
    command = _workflows_command()

    assert "--workers" in command, (
        "workflows must state its worker count explicitly — the Dockerfile CMD defaults to "
        "one, and a compose command that omits the flag hides which value is in force"
    )
    assert command[command.index("--workers") + 1] == "1", (
        "video.py's per-scan lock only serialises within one process; more than one worker "
        "reopens the overwrite race on cyl-videos/{scan_id}.mp4"
    )


def _workflows_service() -> dict:
    compose = yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))
    return compose["services"]["workflows"]


def test_workflows_has_a_memory_limit():
    """`MAX_CONCURRENT_ENCODES` is sized against a container limit that has to
    actually exist.

    A plate frame is ~162 MB decoded at 16-bit and the encoder runs four at
    once, so a render spike is measured in gigabytes. Unbounded, that spike
    goes at the host, and the kernel's OOM killer chooses by resident size —
    which here means db-prod or minio rather than the service that caused it.
    A limit turns a host-wide outage into a failed request.
    """
    service = _workflows_service()

    assert "mem_limit" in service, (
        "plate_encode.py sizes its concurrency limit against a container memory "
        "limit; without one, four concurrent renders can take the host down"
    )
    assert service["mem_limit"].endswith("g"), (
        f"expected a gigabyte-scale limit, got {service['mem_limit']!r}"
    )
    assert int(service["mem_limit"].removesuffix("g")) >= 2, (
        "four 16-bit plates plus the RAM-backed /tmp need more than 1g; a limit "
        "below that fails healthy renders instead of runaway ones"
    )


def test_the_ram_backed_tmpfs_fits_inside_the_memory_limit():
    """`/tmp` is a tmpfs, so the MP4 written there is resident memory and counts
    against the same limit — a tmpfs at or above it leaves nothing for the
    encode that fills it."""
    service = _workflows_service()
    tmp = next(entry for entry in service["tmpfs"] if entry.startswith("/tmp:"))
    size_mb = int(tmp.split("size=")[1].removesuffix("m"))
    limit_mb = int(service["mem_limit"].removesuffix("g")) * 1024

    assert size_mb < limit_mb / 2, (
        f"/tmp is {size_mb}m against a {limit_mb}m limit, leaving too little for "
        "the frames being encoded into it"
    )
