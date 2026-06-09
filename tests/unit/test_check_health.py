"""Unit tests for scripts/check_health.py's pure migration-completeness logic.

The DB/service checks need a live stack (exercised by
tests/integration/test_local_dev_bootstrap.py and `make verify-dev`); here we
pin the set-comparison that must FAIL on a partial migration run — the exact
case a naive "tracking table is non-empty" check would pass.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = REPO_ROOT / "scripts" / "check_health.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_health", _SCRIPT)
    assert spec and spec.loader, f"cannot load {_SCRIPT}"
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check_health = _load()


def test_partial_migration_run_is_a_problem():
    files = {"001", "002", "003"}
    applied = {"001", "002"}  # 003 pending
    problems = check_health.migration_problems(files, applied)
    assert problems, "a pending migration must be reported as a problem"
    assert any("pending" in p for p in problems)


def test_orphan_recorded_migration_is_a_problem():
    files = {"001", "002"}
    applied = {"001", "002", "999"}  # 999 has no file
    problems = check_health.migration_problems(files, applied)
    assert problems
    assert any("orphan" in p for p in problems)


def test_complete_migration_set_has_no_problems():
    versions = {"001", "002", "003"}
    assert check_health.migration_problems(versions, versions) == []


def test_migration_file_versions_reads_real_migrations():
    versions = check_health.migration_file_versions()
    assert versions, "expected migration files under supabase/migrations/"
    assert all(v.isdigit() and len(v) == 14 for v in versions), (
        "every version must be a 14-digit timestamp prefix"
    )


def test_optional_llm_service_unhealthy_is_a_warning_not_a_failure():
    """langchain-agent needs user-supplied LLM config (LOCAL_LLM_URL/OPENAI_API_KEY)
    to ever be healthy, so a fresh `make dev-up` must NOT report the whole stack
    unhealthy because of it — it's a warning."""
    rows = [
        {"Service": "db-dev", "Health": "healthy", "State": "running"},
        {"Service": "langchain-agent", "Health": "unhealthy", "State": "running"},
    ]
    problems, warnings = check_health._classify_service_rows(rows)
    assert not problems, f"optional service must not fail the check: {problems}"
    assert any("langchain-agent" in w for w in warnings)


def test_core_service_unhealthy_is_a_failure():
    rows = [{"Service": "db-dev", "Health": "unhealthy", "State": "running"}]
    problems, warnings = check_health._classify_service_rows(rows)
    assert any("db-dev" in p for p in problems)
    assert not warnings


def test_bloommcp_is_required_not_optional():
    """Once its healthcheck targets /health, bloommcp should be genuinely healthy
    in dev (it has generated keys), so it stays REQUIRED."""
    rows = [{"Service": "bloommcp", "Health": "unhealthy", "State": "running"}]
    problems, warnings = check_health._classify_service_rows(rows)
    assert any("bloommcp" in p for p in problems)


def test_parse_env_ignores_comments_and_blanks():
    text = "# comment\n\nPOSTGRES_USER=supabase_admin\nPOSTGRES_HOST_PORT=5433\n"
    env = check_health.parse_env(text)
    assert env == {"POSTGRES_USER": "supabase_admin", "POSTGRES_HOST_PORT": "5433"}
