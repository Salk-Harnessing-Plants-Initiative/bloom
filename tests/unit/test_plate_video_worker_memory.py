"""The plate video worker's memory limit covers one render at the frame ceilings.

A worker renders one job at a time, so its budget is one encode at the largest
frame the scanner can produce, plus the interpreter, the ffmpeg child and the
RAM-backed /tmp that holds the MP4.
"""

from __future__ import annotations

from pathlib import Path

import re

import yaml

from tests.unit._compose_helpers import _bytes, _mount_options

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"


def _worker() -> dict:
    compose = yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))
    return compose["services"]["plate-video-worker"]


def _max_frame_decoded_bytes() -> int:
    """Read the ceiling the encoder enforces, not a figure recorded beside it.

    A literal here drifts the moment the encoder's own number changes, which is
    how this budget stopped being checked once the ceiling moved.
    """
    source = (REPO_ROOT / "services" / "workflows" / "plate_encode.py").read_text()
    match = re.search(
        r"^MAX_FRAME_DECODED_BYTES = (\d+) \* 1024\*\*2$", source, re.MULTILINE
    )
    assert match, "MAX_FRAME_DECODED_BYTES is no longer a plain literal"
    return int(match.group(1)) * (1 << 20)


def _max_frame_bytes() -> int:
    """Read the download cap, for the same reason as the ceiling above.

    The downloaded object stays resident while the frame is decoded from it, so
    a render costs both at once and the budget has to count both.
    """
    source = (REPO_ROOT / "services" / "workflows" / "plate_encode.py").read_text()
    match = re.search(r"^MAX_FRAME_BYTES = (\d+) \* 1024\*\*2$", source, re.MULTILINE)
    assert match, "MAX_FRAME_BYTES is no longer a plain literal"
    return int(match.group(1)) * (1 << 20)


# What the interpreter and one ffmpeg child cost, measured as peak RSS: 69 MB
# for the service's imports, 54 MB per child at 1440x1990.
BASELINE_BYTES = 69 * (1 << 20)
PER_FFMPEG_BYTES = 54 * (1 << 20)


def test_the_worker_has_a_memory_limit():
    """Unbounded, a render spike goes at the host and the kernel's OOM killer
    chooses by resident size, which is not this worker. A limit turns that into
    a failed job in the container that caused it.

    The floor is 4g because 2g is not enough: a real plate render is SIGKILLed
    there. The arithmetic below — one render at the frame ceilings and the
    tmpfs, about 1.4 GB — is optimistic about what a full-resolution GraviScan
    TIFF costs.
    """
    service = _worker()

    assert "mem_limit" in service, (
        "plate_encode.py sizes its frame ceilings against a container memory "
        "limit; without one, a render spike can take the host down"
    )
    assert _bytes(service["mem_limit"]) >= 4 * (1 << 30), (
        f"the limit is {service['mem_limit']!r}; a real plate render is killed "
        "at 2g, so a limit at or below that fails healthy renders rather than "
        "runaway ones"
    )


def test_swap_cannot_lift_the_container_past_its_memory_limit():
    """Docker allows twice mem_limit in swap when no swap limit is set.

    A runaway render would then grind against disk instead of being killed,
    slowing every other container on the box indefinitely — the host-wide harm
    the memory limit is here to prevent. Equal values mean no swap.
    """
    service = _worker()

    assert "memswap_limit" in service, (
        "without memswap_limit, mem_limit is a soft ceiling: Docker permits "
        "2x it in swap and a runaway render thrashes rather than stopping"
    )
    assert _bytes(service["memswap_limit"]) == _bytes(service["mem_limit"]), (
        f"memswap_limit {service['memswap_limit']!r} against mem_limit "
        f"{service['mem_limit']!r} — anything larger is swap headroom"
    )


def test_the_memory_limit_covers_one_render_at_the_frame_ceilings():
    """The limit and the frame ceilings have to move together."""
    service = _worker()
    tmpfs = next(e for e in service["tmpfs"] if e.startswith("/tmp:"))
    per_encode = _max_frame_decoded_bytes() + _max_frame_bytes() + PER_FFMPEG_BYTES
    needed = per_encode + BASELINE_BYTES + _bytes(
        _mount_options(tmpfs)["size"]
    )

    assert _bytes(service["mem_limit"]) >= needed, (
        "one encode at the frame ceiling needs about "
        f"{needed // (1 << 20)}m with the downloaded objects, the interpreter, "
        f"the ffmpeg children and the tmpfs, against a limit of "
        f"{service['mem_limit']!r}. Raise the limit or lower one of the frame "
        "ceilings."
    )


def test_the_ram_backed_tmpfs_fits_inside_the_memory_limit():
    """`/tmp` is a tmpfs, so the MP4 written there is resident memory and counts
    against the same limit — a tmpfs at or above it leaves nothing for the
    encode that fills it."""
    service = _worker()
    tmpfs = next(entry for entry in service["tmpfs"] if entry.startswith("/tmp:"))
    size = _bytes(_mount_options(tmpfs)["size"])
    limit = _bytes(service["mem_limit"])

    assert size < limit / 2, (
        f"/tmp is {size // (1 << 20)}m against a {limit // (1 << 20)}m limit, "
        "leaving too little for the frames being encoded into it"
    )
