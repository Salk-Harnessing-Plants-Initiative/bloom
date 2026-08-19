"""Regression guard for ``.github/workflows/refresh-cyl-experiment-trait-counts.yml``.

bloom#637/#656 (fix-cyl-scan-traits-latest-rollup, design.md D8): this workflow
originally read its staging base URL from ``secrets.STAGING_API_URL``, a secret
that was never actually provisioned. ``API_EXTERNAL_URL`` in
``.env.staging.defaults`` is already the same value, already committed, and
already documented as non-sensitive by the Committed Defaults contract (see
``tests/unit/test_env_defaults.py``) -- there is nothing to protect by putting
it behind a GitHub secret. This workflow also deliberately never checks out
the repo (``permissions: {}``, an outbound-curl-only job), so it can't just
read ``.env.staging.defaults`` at runtime either; the value is hardcoded as a
literal in the workflow YAML instead.

This test does two things:

1. Asserts the workflow no longer references ``secrets.STAGING_API_URL`` at
   all, and that the literal it hardcodes in its place matches
   ``API_EXTERNAL_URL`` in ``.env.staging.defaults`` -- so the two can never
   silently drift apart.
2. Asserts the surrounding shape (service-role key still a real secret, the
   https:// guard, ``permissions: {}``, no checkout step, the once-daily
   schedule) so an unrelated future edit can't quietly widen this job's
   permissions or reintroduce the secret dependency it just shed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "refresh-cyl-experiment-trait-counts.yml"
STAGING_DEFAULTS = REPO_ROOT / ".env.staging.defaults"
JOB = "refresh"


def _parse_env(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE file into a dict. Ignore blank/comment lines."""
    result: dict[str, str] = {}
    for raw in path.read_text().splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        result[key.strip()] = value.strip()
    return result


def _load_workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _step(workflow: dict) -> dict:
    steps = workflow["jobs"][JOB]["steps"]
    assert len(steps) == 1, f"expected exactly one step in job {JOB!r}, got {len(steps)}"
    return steps[0]


def test_no_longer_references_a_staging_api_url_secret() -> None:
    """The secret was never provisioned; the workflow must not depend on it."""
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets.STAGING_API_URL" not in text, (
        "workflow still references secrets.STAGING_API_URL -- that secret was "
        "never provisioned (see PR discussion); the base URL should be a "
        "hardcoded literal instead, since it is public, stable config already "
        "committed in .env.staging.defaults."
    )


def test_staging_api_url_is_hardcoded_and_matches_env_defaults() -> None:
    """The hardcoded literal must match .env.staging.defaults's API_EXTERNAL_URL."""
    env = _step(_load_workflow())["env"]
    hardcoded = env.get("STAGING_API_URL")
    assert hardcoded is not None, "step must set env.STAGING_API_URL"
    assert not str(hardcoded).startswith("${{"), (
        f"STAGING_API_URL must be a literal string, not a GitHub Actions "
        f"expression; got {hardcoded!r}"
    )
    expected = _parse_env(STAGING_DEFAULTS)["API_EXTERNAL_URL"]
    assert hardcoded == expected, (
        f"workflow's hardcoded STAGING_API_URL ({hardcoded!r}) has drifted "
        f"from .env.staging.defaults's API_EXTERNAL_URL ({expected!r}) -- "
        "these must stay in sync since they name the same staging host."
    )
    assert hardcoded.startswith("https://"), (
        "the hardcoded literal itself must be https:// -- SERVICE_ROLE_KEY is "
        "a full-bypass-RLS credential sent on every scheduled run."
    )


def test_service_role_key_still_sourced_from_a_real_secret() -> None:
    """Only the non-sensitive URL was hardcoded -- the actual credential stays secret."""
    env = _step(_load_workflow())["env"]
    assert env.get("SERVICE_ROLE_KEY") == "${{ secrets.STAGING_SERVICE_ROLE_KEY }}", (
        f"SERVICE_ROLE_KEY must still come from secrets.STAGING_SERVICE_ROLE_KEY; "
        f"got {env.get('SERVICE_ROLE_KEY')!r}"
    )


def test_https_guard_still_present_in_script() -> None:
    """A defensive check survives even though the literal is now visibly https://."""
    script = _step(_load_workflow())["run"]
    assert "https://*" in script and "must be https://" in script, (
        "the run script's https:// guard on STAGING_API_URL should stay in "
        "place as a defensive check against a future careless edit to the "
        "hardcoded literal."
    )


def test_job_still_has_no_checkout_and_empty_permissions() -> None:
    """Hardcoding the literal must not require adding a checkout step."""
    workflow = _load_workflow()
    perms = workflow.get("permissions")
    assert perms == {}, (
        f"job permissions must stay empty ({{}}) -- this job only makes an "
        f"outbound curl call and should never need repo-content access; got "
        f"{perms!r}"
    )
    steps = workflow["jobs"][JOB]["steps"]
    assert not any("actions/checkout" in str(s.get("uses", "")) for s in steps), (
        "no step should use actions/checkout -- the hardcoded literal makes "
        "checkout unnecessary; adding one would silently widen this job's "
        "blast radius for a value that never needed repo access."
    )


def test_schedule_is_once_daily() -> None:
    """Locks in the once-daily cadence (not the original 5-15 min proposal)."""
    on = _load_workflow().get("on") or _load_workflow().get(True)
    schedule = on["schedule"]
    assert len(schedule) == 1
    assert schedule[0]["cron"] == "0 6 * * *", (
        f"expected a single once-daily cron entry '0 6 * * *'; got {schedule!r}"
    )
