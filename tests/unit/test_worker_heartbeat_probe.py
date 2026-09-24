"""The two cylinder workers must keep a health signal, and it must be a real one.

cyl-pipeline-worker and cyl-status-poller are `while` loops with no HTTP server.
For a long time they carried no healthcheck at all, so `docker compose ps` could
only report that the process existed — and a process that exists proves nothing:
a loop wedged on a hung socket looks exactly like one doing its job. Both also
carry `restart: unless-stopped`, so a crash presents as an endless `restarting`
state rather than an exit (issue #163).

These pin the invariant, not the wording: each service has a healthcheck, it
runs the heartbeat probe with an interpreter the image actually ships, and its
staleness ceiling clears the slowest legitimate iteration by a real margin.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = (
    REPO_ROOT / "docker-compose.dev.yml",
    REPO_ROOT / "docker-compose.prod.yml",
)
PROBE = REPO_ROOT / "services" / "workflows" / "heartbeat.py"

# The workers' poll intervals (their Python defaults), and the ceiling each
# probe allows. A sweep or a submission takes far longer than one poll: the
# dispatch POST alone carries a 15s timeout plus RPC round trips.
SERVICES = {
    "cyl-pipeline-worker": {"poll": 5.0, "max_age": 90.0},
    "cyl-status-poller": {"poll": 15.0, "max_age": 180.0},
}

# python:3.11-slim ships these; a probe naming anything else needs an install
# step, which is how langchain-agent's curl probe broke (see
# test_langchain_healthcheck_probe.py).
ALWAYS_PRESENT = frozenset({"python", "python3"})


def _service(compose_path: Path, name: str) -> dict:
    spec = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    return spec["services"][name] or {}


def _argv(compose_path: Path, name: str) -> list[str]:
    hc = _service(compose_path, name).get("healthcheck")
    assert hc, f"{compose_path.name}: {name} has no healthcheck — a running loop is not a working one"
    test = hc["test"]
    assert isinstance(test, list) and test, f"{compose_path.name}: expected a list-form test"
    return [a for a in test if a not in ("CMD", "CMD-SHELL")]


@pytest.mark.parametrize("compose_path", COMPOSE_FILES, ids=lambda p: p.name)
@pytest.mark.parametrize("name", sorted(SERVICES))
def test_the_worker_has_a_healthcheck(compose_path, name):
    argv = _argv(compose_path, name)
    assert argv[0] in ALWAYS_PRESENT, (
        f"{name}'s probe runs {argv[0]!r}, which python:3.11-slim may not ship"
    )
    assert any("heartbeat.py" in a for a in argv), (
        f"{name}'s probe must read the heartbeat; a process-liveness check adds "
        "nothing over the container already being up"
    )


@pytest.mark.parametrize("compose_path", COMPOSE_FILES, ids=lambda p: p.name)
@pytest.mark.parametrize("name", sorted(SERVICES))
def test_the_probe_script_exists_in_the_build_context(compose_path, name):
    """The probe runs from WORKDIR /app, which is a COPY of services/workflows."""
    assert PROBE.exists(), f"{PROBE} is missing, so every probe would exit non-zero"
    svc = _service(compose_path, name)
    context = (svc.get("build") or {}).get("context")
    assert context == "./services/workflows", (
        f"{name} builds from {context!r}; the probe path assumes ./services/workflows"
    )


@pytest.mark.parametrize("compose_path", COMPOSE_FILES, ids=lambda p: p.name)
@pytest.mark.parametrize("name", sorted(SERVICES))
def test_the_staleness_ceiling_clears_the_slowest_iteration(compose_path, name):
    """Too tight a ceiling flaps, and a flapping probe fails prod deploys via
    `up --wait` rather than catching anything."""
    argv = _argv(compose_path, name)
    assert "--max-age" in argv, f"{name}'s probe must set an explicit ceiling"
    max_age = float(argv[argv.index("--max-age") + 1])
    expected = SERVICES[name]
    assert max_age == expected["max_age"], (
        f"{name} allows {max_age}s; the reviewed value is {expected['max_age']}s"
    )
    assert max_age >= expected["poll"] * 6, (
        f"{name} allows {max_age}s against a {expected['poll']}s poll — too "
        "little headroom for one slow iteration"
    )


@pytest.mark.parametrize("compose_path", COMPOSE_FILES, ids=lambda p: p.name)
@pytest.mark.parametrize("name", sorted(SERVICES))
def test_the_probe_gets_a_start_period(compose_path, name):
    """The first heartbeat lands after the startup connect, which retries
    through a Supabase outage — without a grace period that reads as failure."""
    hc = _service(compose_path, name)["healthcheck"]
    assert hc.get("start_period"), f"{name}'s healthcheck needs a start_period"


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_dev_and_prod_probe_the_worker_identically(name):
    """A probe that only exists in one file is one nobody tests before deploying."""
    dev, prod = (_argv(p, name) for p in COMPOSE_FILES)
    assert dev == prod, f"{name}: dev probes {dev}, prod probes {prod}"


@pytest.mark.parametrize("name", sorted(SERVICES))
def test_the_worker_can_write_its_heartbeat_under_read_only(name):
    """Both run with `read_only: true`, so the heartbeat's directory has to be
    one of the tmpfs mounts or every write fails silently and the probe fails."""
    for compose_path in COMPOSE_FILES:
        svc = _service(compose_path, name)
        if not svc.get("read_only"):
            continue
        mounts = [m.split(":", 1)[0] for m in (svc.get("tmpfs") or [])]
        assert "/tmp" in mounts, (
            f"{compose_path.name}: {name} is read_only with tmpfs {mounts}; the "
            "heartbeat defaults to /tmp and would never be written"
        )
