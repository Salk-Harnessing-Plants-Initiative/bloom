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
    # Compose accepts b/kb/mb/gb as well as the bare letter, so `2gb` and `2g`
    # are one limit. Dropping a trailing `b` first keeps the lookup to one form.
    if text.endswith("b") and len(text) > 1 and text[-2].isalpha():
        text = text[:-1]
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


def test_workflows_has_a_memory_limit():
    """`MAX_CONCURRENT_ENCODES` is sized against a container limit that has to
    actually exist.

    Unbounded, a render spike goes at the host and the kernel's OOM killer
    chooses by resident size, which is not this service. A limit turns that
    into a failed request in the container that caused it.

    The floor is 4g because 2g is not enough: a real plate render is SIGKILLed
    there. The arithmetic below — one render at the frame ceilings costing about
    1 GB — is optimistic about what a full-resolution GraviScan TIFF costs.
    """
    service = _workflows_service()

    assert "mem_limit" in service, (
        "plate_encode.py sizes its concurrency limit against a container memory "
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
    per_encode = _max_frame_decoded_bytes() + _max_frame_bytes() + PER_FFMPEG_BYTES
    needed = concurrent * per_encode + BASELINE_BYTES + _bytes(
        _mount_options(tmpfs)["size"]
    )

    assert _bytes(service["mem_limit"]) >= needed, (
        f"{concurrent} encodes at the frame ceiling need about "
        f"{needed // (1 << 20)}m with the downloaded objects, the interpreter, "
        f"the ffmpeg children and the tmpfs, against a limit of "
        f"{service['mem_limit']!r}. Raise the limit, lower "
        "MAX_CONCURRENT_ENCODES, or lower one of the frame ceilings."
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


def test_the_progress_record_matches_the_worker_count():
    """`plate_progress` keeps one slot, not a registry.

    That is only correct while this service runs one uvicorn worker: a second
    worker has its own module state, so two renders could each believe they are
    the only one and report their frames under the other's plate.
    """
    service = _workflows_service()
    command = service["command"]

    assert command[command.index("--workers") + 1] == "1", (
        "plate_progress keeps a single in-memory slot; more than one worker "
        "makes it describe the wrong plate"
    )
