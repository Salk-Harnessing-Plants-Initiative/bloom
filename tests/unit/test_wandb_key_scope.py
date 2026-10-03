"""WANDB_API_KEY reaches the workflows service and nothing else.

GET /model-cards (bloom#971) reads the production model cards from the wandb
registry with it. Seven containers share the workflows image, but only the
`workflows` service serves that route, so it is the only one given the key.
Prod and staging both deploy from docker-compose.prod.yml, where
scripts/validate_env.sh requires every referenced variable to be set, so the key
is required there; dev's compose makes it optional.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
KEY = "WANDB_API_KEY"
EXPECTED = {
    "docker-compose.prod.yml": "${WANDB_API_KEY}",
    "docker-compose.dev.yml": "${WANDB_API_KEY:-}",
}


def _services(compose_file: str) -> dict:
    return yaml.safe_load((REPO_ROOT / compose_file).read_text(encoding="utf-8"))[
        "services"
    ]


def _environment(service: dict) -> dict:
    env = service.get("environment") or {}
    if isinstance(env, list):
        return dict(item.split("=", 1) if "=" in item else (item, None) for item in env)
    return env


@pytest.mark.parametrize("compose_file", sorted(EXPECTED))
def test_workflows_gets_the_key(compose_file):
    env = _environment(_services(compose_file)["workflows"])
    assert env.get(KEY) == EXPECTED[compose_file]


@pytest.mark.parametrize("compose_file", sorted(EXPECTED))
def test_no_other_service_gets_the_key(compose_file):
    holders = sorted(
        name
        for name, service in _services(compose_file).items()
        if KEY in _environment(service)
    )
    assert holders == ["workflows"]


@pytest.mark.parametrize("prefix", ["PROD", "STAGING"])
def test_each_deploy_heredoc_writes_the_key(prefix):
    deploy = (REPO_ROOT / ".github/workflows/deploy.yml").read_text(encoding="utf-8")
    line = f"{KEY}=${{{{ secrets.{prefix}_{KEY} }}}}"
    assert sum(row.strip() == line for row in deploy.splitlines()) == 1, line
