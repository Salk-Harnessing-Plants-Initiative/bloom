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

import re

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


def _bytes(value) -> int:
    """A compose size as bytes. `2g`, `2G`, `2048m` and 2147483648 are one limit.

    Parsed rather than string-matched so a correct edit in a different spelling
    is not reported as a broken one — a test that accepts only the spelling it
    was written against is a trap for whoever touches the file next.
    """
    if isinstance(value, int):
        return value
    text = str(value).strip().lower()
    for suffix, scale in (("g", 1 << 30), ("m", 1 << 20), ("k", 1 << 10), ("b", 1)):
        if text.endswith(suffix):
            return int(float(text[: -len(suffix)]) * scale)
    return int(text)


def _mount_options(entry: str) -> dict[str, str]:
    """The options of a tmpfs mount, by name — order is not meaning."""
    _, _, options = entry.partition(":")
    parsed = {}
    for option in options.split(","):
        name, _, value = option.partition("=")
        parsed[name] = value
    return parsed


def _max_concurrent_encodes() -> int:
    """Read the encoder's own limit, so the two cannot drift apart silently."""
    source = (REPO_ROOT / "services" / "workflows" / "plate_encode.py").read_text()
    match = re.search(r"^MAX_CONCURRENT_ENCODES = (\d+)$", source, re.MULTILINE)
    assert match, "MAX_CONCURRENT_ENCODES is no longer a plain literal"
    return int(match.group(1))


# What one render costs at 16-bit, all in: ~162 MB of frame measured by
# tracemalloc, plus Pillow's decode buffer, which is allocated outside the
# Python allocator and so is not in that figure. Recorded in plate_encode.py's
# MAX_CONCURRENT_ENCODES comment.
PER_ENCODE_BYTES = 350 * (1 << 20)


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
    assert _bytes(service["mem_limit"]) >= 2 * (1 << 30), (
        f"the limit is {service['mem_limit']!r}; four 16-bit plates plus the "
        "RAM-backed /tmp need more than 1g, and a limit below that fails "
        "healthy renders rather than runaway ones"
    )


def test_swap_cannot_lift_the_container_past_its_memory_limit():
    """Docker allows twice mem_limit in swap when no swap limit is set.

    A runaway render would then grind against disk instead of being killed,
    slowing every other container on the box indefinitely — the host-wide harm
    the memory limit is here to prevent. Equal values mean no swap.
    """
    service = _workflows_service()

    assert "memswap_limit" in service, (
        "without memswap_limit, mem_limit is a soft ceiling: Docker permits "
        "2x it in swap and a runaway render thrashes rather than stopping"
    )
    assert _bytes(service["memswap_limit"]) == _bytes(service["mem_limit"]), (
        f"memswap_limit {service['memswap_limit']!r} against mem_limit "
        f"{service['mem_limit']!r} — anything larger is swap headroom"
    )


def test_the_memory_limit_covers_the_encodes_the_service_allows():
    """The limit and the concurrency cap have to move together.

    plate_encode.py's comment calls its cap "the multiplier on a render's
    memory ... where a container limit has to be read from". Nothing enforced
    that: doubling the cap left the limit untouched and every test green.
    """
    service = _workflows_service()
    concurrent = _max_concurrent_encodes()
    tmpfs = next(e for e in service["tmpfs"] if e.startswith("/tmp:"))
    needed = concurrent * PER_ENCODE_BYTES + _bytes(_mount_options(tmpfs)["size"])

    assert _bytes(service["mem_limit"]) >= needed, (
        f"{concurrent} concurrent encodes need about {needed // (1 << 20)}m "
        f"with the tmpfs, against a limit of {service['mem_limit']!r}. Raise "
        "the limit or lower MAX_CONCURRENT_ENCODES."
    )


def test_the_ram_backed_tmpfs_fits_inside_the_memory_limit():
    """`/tmp` is a tmpfs, so the MP4 written there is resident memory and counts
    against the same limit — a tmpfs at or above it leaves nothing for the
    encode that fills it."""
    service = _workflows_service()
    tmpfs = next(entry for entry in service["tmpfs"] if entry.startswith("/tmp:"))
    size = _bytes(_mount_options(tmpfs)["size"])
    limit = _bytes(service["mem_limit"])

    assert size < limit / 2, (
        f"/tmp is {size // (1 << 20)}m against a {limit // (1 << 20)}m limit, "
        "leaving too little for the frames being encoded into it"
    )
