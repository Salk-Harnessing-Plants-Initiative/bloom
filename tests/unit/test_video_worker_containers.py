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

from tests.unit._compose_helpers import _bytes, _mount_options

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
@pytest.mark.parametrize("worker", VIDEO_WORKERS + ("cyl-pipeline-worker", "cyl-status-poller"))
def test_the_ram_backed_tmpfs_fits_well_inside_the_memory_limit(compose_file, worker):
    """The same rule the workflows service is held to. tmpfs pages are charged to
    the container's own cgroup, so a limit that merely exceeds the mount turns a
    full /tmp into an OOM kill instead of the write error the size cap gives."""
    service = _services(compose_file)[worker]

    assert "mem_limit" in service, f"{worker} is uncapped in {compose_file}"
    tmpfs_bytes = _bytes(_mount_options(service["tmpfs"][0])["size"])
    assert tmpfs_bytes < _bytes(service["mem_limit"]) / 2, (
        f"{worker}'s /tmp is {service['tmpfs'][0]!r} against a "
        f"{service['mem_limit']!r} limit in {compose_file}; a full /tmp then "
        "leaves too little for the process itself"
    )
    assert _bytes(service["memswap_limit"]) == _bytes(service["mem_limit"])


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
@pytest.mark.parametrize("worker", VIDEO_WORKERS)
def test_the_worker_carries_the_services_own_credentials(compose_file, worker):
    """Without these the render cannot log in, and nothing else in the suite
    notices: the failure appears only when someone runs the command."""
    services = _services(compose_file)
    expected = dict(services["workflows"]["environment"])
    # The workers serve no HTTP, so the one variable they legitimately drop.
    expected.pop("WORKFLOWS_CORS_ORIGINS", None)

    assert services[worker]["environment"] == expected, (
        f"{worker}'s environment in {compose_file} has drifted from the "
        "workflows service's; the render reads the same variables"
    )


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
@pytest.mark.parametrize("worker", VIDEO_WORKERS)
def test_the_worker_is_on_the_services_network(compose_file, worker):
    """Off supanet the container cannot reach Kong at all, and every render
    fails at the first request."""
    services = _services(compose_file)

    assert services[worker].get("networks") == services["workflows"].get("networks"), (
        f"{worker} is not on the workflows service's network in {compose_file}"
    )


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
@pytest.mark.parametrize(
    "worker,script",
    (
        ("plate-video-worker", "plate_video_worker.py"),
        ("cyl-video-worker", "cyl_video_worker.py"),
    ),
)
def test_the_default_command_prints_usage_rather_than_rendering(
    compose_file, worker, script
):
    """Rendering takes arguments, so there is no safe default render. A usage
    message exits 0 immediately; anything long-running would make a profiled
    start look like a working service."""
    command = _services(compose_file)[worker]["command"]

    assert script in command, f"{worker} runs {command!r} in {compose_file}"
    assert command[-1] == "--help", (
        f"{worker}'s default command is {command!r}; it must not render or idle"
    )


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
@pytest.mark.parametrize("worker", VIDEO_WORKERS)
def test_the_worker_does_not_restart(compose_file, worker):
    """With a usage message as the default command, a restart policy would turn
    a profiled start into a tight exit-0 loop."""
    assert "restart" not in _services(compose_file)[worker], (
        f"{worker} has a restart policy in {compose_file}"
    )
