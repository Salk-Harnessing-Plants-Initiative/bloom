"""storage must wait for minio-init to finish, or `docker compose up --wait` is a coin toss.

minio-init is a one-shot: it creates the storage buckets and exits 0. The deploy
runs `up --wait`, which accepts a container that is still running but fails on one
that has already exited, unless another service waits for it to complete. So a
deploy passed when minio-init was still running at the moment compose checked, and
failed with `minio-init exited (0)` when it had finished first; the rollback then
failed the same way. storage is the service that needs those buckets, so it is
the one that waits.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
PROD_COMPOSE = REPO_ROOT / "docker-compose.prod.yml"
DEPLOY = REPO_ROOT / ".github" / "workflows" / "deploy.yml"


def _services() -> dict:
    return yaml.safe_load(PROD_COMPOSE.read_text(encoding="utf-8"))["services"]


def test_storage_waits_for_minio_init_to_complete():
    depends_on = _services()["storage"].get("depends_on") or {}
    assert depends_on.get("minio-init") == {
        "condition": "service_completed_successfully"
    }


def test_minio_init_is_a_one_shot():
    """A restart policy would keep it running, and the dependency would never complete."""
    assert _services()["minio-init"].get("restart", "no") == "no"


def test_the_deploy_still_waits_on_the_stack():
    """The reason for the dependency; if the deploy stops using --wait, revisit it."""
    assert "--wait" in DEPLOY.read_text(encoding="utf-8")
