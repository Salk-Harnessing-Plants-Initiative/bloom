"""The prod migration push applies every pending migration, including one dated before the newest
migration prod already has: staging sets the order, so a promotion can bring such a migration."""

from __future__ import annotations

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).parent.parent.parent / ".github" / "workflows" / "deploy.yml"


def _step(job: str, step_id: str) -> str:
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"][job]["steps"]
    (step,) = [s for s in steps if s.get("id") == step_id]
    return step["run"]


def test_the_prod_push_includes_migrations_older_than_prods_newest():
    assert "prod 'db push --debug --yes --include-all'" in _step("deploy-production", "migrate_prod")


def test_the_staging_push_is_unchanged():
    run = _step("deploy-staging", "migrate_staging")
    assert "staging 'db push --debug --yes'" in run and "--include-all" not in run
