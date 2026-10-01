"""The rnaseq status poller runs whenever the stack runs and records RNA-seq run status.

It reads the cluster as the same account as cyl-status-poller, so it is held to the
same container rules: hardened like the workflows service, capped, and given time to
finish the run in progress on shutdown.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from tests.unit._compose_helpers import _bytes, _mount_options

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ("docker-compose.prod.yml", "docker-compose.dev.yml")
WORKER = "rnaseq-status-poller"
REFERENCE = "cyl-status-poller"


def _services(compose_file: str) -> dict:
    return yaml.safe_load((REPO_ROOT / compose_file).read_text(encoding="utf-8"))[
        "services"
    ]


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_the_poller_starts_with_the_stack(compose_file):
    service = _services(compose_file)[WORKER]
    assert "profiles" not in service, f"{WORKER} would not start on `up -d`"
    assert service.get("restart") == "unless-stopped"


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_the_poller_runs_off_the_workflows_image(compose_file):
    services = _services(compose_file)
    service = services[WORKER]
    assert service["build"] == services["workflows"]["build"]
    assert service["command"] == ["python", "rnaseq_status_poller.py"]


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_the_poller_has_the_cyl_pollers_environment(compose_file):
    """Both read the same cluster as the same account."""
    services = _services(compose_file)
    assert services[WORKER]["environment"] == services[REFERENCE]["environment"], (
        f"{WORKER}'s environment has drifted from {REFERENCE}'s in {compose_file}"
    )


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_the_poller_is_hardened_like_the_service(compose_file):
    services = _services(compose_file)
    service = services[WORKER]
    assert service.get("read_only") is True
    assert service.get("cap_drop") == ["ALL"]
    assert "no-new-privileges:true" in service.get("security_opt", [])
    assert service.get("networks") == services["workflows"].get("networks")
    # It writes nothing to /tmp, so it gets the cyl poller's small RAM disk.
    assert _mount_options(service["tmpfs"][0]) == _mount_options(
        services[REFERENCE]["tmpfs"][0]
    )


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_the_poller_is_capped_and_its_tmpfs_fits_inside_the_cap(compose_file):
    service = _services(compose_file)[WORKER]
    limit = _bytes(service["mem_limit"])
    assert _bytes(service["memswap_limit"]) == limit
    tmpfs_bytes = _bytes(_mount_options(service["tmpfs"][0])["size"])
    assert tmpfs_bytes < limit / 2


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
def test_a_shutdown_waits_for_the_run_in_progress(compose_file):
    """A status read can take up to 15 s plus its update; Docker's default 10 s would cut it off."""
    services = _services(compose_file)
    assert services[WORKER].get("stop_grace_period") == services[REFERENCE].get(
        "stop_grace_period"
    )


def test_the_dev_poller_runs_its_built_image_not_the_mounted_source():
    assert not _services("docker-compose.dev.yml")[WORKER].get("volumes")
