"""The video workers are containers off the workflows image that a deploy never starts.

A render outgrew the request that asked for it: it holds gigabytes, outlives the
proxy timeout, and dies with the web service on every deploy. Moving it into its
own container is only safe if the container is hardened the way the service is,
carries a limit of its own, and stays out of `docker compose up -d` until a queue
exists to feed it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.unit.test_workflows_single_worker import _bytes, _mount_options

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ("docker-compose.prod.yml", "docker-compose.dev.yml")
VIDEO_WORKERS = ("plate-video-worker", "cyl-video-worker")
# The floor the workflows service is held to; a render is SIGKILLed at 2g.
MEMORY_FLOOR_BYTES = 4 * (1 << 30)


def _services(compose_file: str) -> dict:
    return yaml.safe_load((REPO_ROOT / compose_file).read_text(encoding="utf-8"))[
        "services"
    ]


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
@pytest.mark.parametrize("worker", VIDEO_WORKERS)
def test_the_worker_is_defined(compose_file, worker):
    assert worker in _services(compose_file), (
        f"{worker} is missing from {compose_file}; a worker defined in one file only "
        "renders in one environment"
    )


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
@pytest.mark.parametrize("worker", VIDEO_WORKERS)
def test_a_deploy_does_not_start_the_worker(compose_file, worker):
    """Deploys run `up -d` with no `--profile`, which skips a profiled service
    entirely. Until a queue exists, a running worker would have nothing to claim."""
    service = _services(compose_file)[worker]

    assert service.get("profiles"), (
        f"{worker} has no profile, so `up -d` would start it in {compose_file} — "
        "with no queue to claim from, and no command but a usage message"
    )


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
@pytest.mark.parametrize("worker", VIDEO_WORKERS)
def test_the_worker_is_built_from_the_workflows_image(compose_file, worker):
    """The render code and its ffmpeg and imaging dependencies already live in that
    image. A second build context would duplicate them and drift."""
    services = _services(compose_file)

    assert services[worker]["build"] == services["workflows"]["build"], (
        f"{worker} builds from something other than the workflows image in "
        f"{compose_file}; then its renderer is not the one the route runs"
    )


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
@pytest.mark.parametrize("worker", VIDEO_WORKERS)
def test_the_worker_does_not_inherit_the_web_server_command(compose_file, worker):
    """The image's CMD is uvicorn. A worker that inherits it serves HTTP instead of
    rendering, and nothing would say so."""
    command = _services(compose_file)[worker].get("command")

    assert command, f"{worker} has no command in {compose_file}, so it runs uvicorn"
    assert "uvicorn" not in command, f"{worker} runs the web server in {compose_file}"


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
@pytest.mark.parametrize("worker", VIDEO_WORKERS)
def test_the_worker_has_a_memory_limit_that_swap_cannot_lift(compose_file, worker):
    """Same reasoning as the service's own limit: unbounded, a render spike goes at
    the host, and without a swap limit Docker allows twice the ceiling in swap."""
    service = _services(compose_file)[worker]

    assert "mem_limit" in service, f"{worker} is uncapped in {compose_file}"
    assert _bytes(service["mem_limit"]) >= MEMORY_FLOOR_BYTES, (
        f"{worker} is capped at {service['mem_limit']!r} in {compose_file}; a real "
        "plate render is killed at 2g"
    )
    assert _bytes(service["memswap_limit"]) == _bytes(service["mem_limit"]), (
        f"{worker}'s memswap_limit in {compose_file} leaves swap headroom, so a "
        "runaway render thrashes rather than stopping"
    )


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
@pytest.mark.parametrize("worker", VIDEO_WORKERS)
def test_the_worker_is_hardened_like_the_service(compose_file, worker):
    """It runs the same code on the same attacker-influenced image bytes."""
    services = _services(compose_file)
    service, reference = services[worker], services["workflows"]

    assert service.get("read_only") is True, f"{worker} has a writable root filesystem"
    assert service.get("cap_drop") == ["ALL"], f"{worker} keeps Linux capabilities"
    assert "no-new-privileges:true" in service.get("security_opt", []), (
        f"{worker} allows privilege escalation in {compose_file}"
    )
    assert _mount_options(service["tmpfs"][0]) == _mount_options(
        reference["tmpfs"][0]
    ), f"{worker}'s /tmp differs from the service's in {compose_file}"


@pytest.mark.parametrize("worker", VIDEO_WORKERS)
def test_the_dev_worker_runs_its_built_image_not_the_mounted_source(worker):
    """The dev `workflows` service mounts the source over the image for `--reload`,
    which crash-loops whenever the image's venv is older than what the source
    imports. A worker has no reload to gain from it."""
    service = _services("docker-compose.dev.yml")[worker]

    assert not service.get("volumes"), (
        f"{worker} mounts the source over its image in dev; a stale image then "
        "fails on an import the source has already moved past"
    )


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
@pytest.mark.parametrize("worker", ("cyl-pipeline-worker", "cyl-status-poller"))
def test_the_pipeline_containers_are_capped_above_their_tmpfs(compose_file, worker):
    """Carried from the closed memory-limits PR. tmpfs pages are charged to the
    container's own cgroup, so a ceiling at or below the mount turns a full /tmp
    into an OOM kill instead of the write error the size cap is meant to give."""
    service = _services(compose_file)[worker]

    assert "mem_limit" in service, f"{worker} is uncapped in {compose_file}"
    tmpfs_bytes = _bytes(_mount_options(service["tmpfs"][0])["size"])
    assert _bytes(service["mem_limit"]) > tmpfs_bytes, (
        f"{worker}'s limit in {compose_file} does not clear its own /tmp"
    )
    assert _bytes(service["memswap_limit"]) == _bytes(service["mem_limit"])
