"""cyl-status-poller must see the same cluster settings the dispatcher stamps.

The poller trusts a Workflow as a run's own only when its `environment` label
matches the poller's own WORKFLOWS_K8S_ENV_LABEL, and never treats a workflow as
removed sooner than WORKFLOWS_K8S_TTL_SECONDS after dispatch
(fix-cyl-poller-unconcluded-runs, bloom#1042). Left unset, ENV_LABEL defaults to
"dev" in the poller while cyl-pipeline-worker stamps "staging"/"prod", and every
live workflow would read as gone (PR #1048 review).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILES = ("docker-compose.prod.yml", "docker-compose.dev.yml")
POLLER = "cyl-status-poller"
DISPATCHER = "cyl-pipeline-worker"
SHARED = (
    "WORKFLOWS_K8S_NAMESPACE",
    "WORKFLOWS_K8S_ENV_LABEL",
    "WORKFLOWS_K8S_TTL_SECONDS",
)


def _services(compose_file: str) -> dict:
    return yaml.safe_load((REPO_ROOT / compose_file).read_text(encoding="utf-8"))[
        "services"
    ]


@pytest.mark.parametrize("compose_file", COMPOSE_FILES)
@pytest.mark.parametrize("variable", SHARED)
def test_the_poller_reads_the_dispatchers_cluster_settings(compose_file, variable):
    services = _services(compose_file)
    poller = services[POLLER]["environment"]
    dispatcher = services[DISPATCHER]["environment"]
    assert variable in poller, f"{POLLER} does not get {variable} in {compose_file}"
    assert poller[variable] == dispatcher[variable], (
        f"{POLLER} and {DISPATCHER} resolve {variable} differently in {compose_file}"
    )
