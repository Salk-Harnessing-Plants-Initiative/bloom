"""Unit tests for scripts/check_health.py's pure migration-completeness logic.

The DB/service checks need a live stack (exercised by
tests/integration/test_local_dev_bootstrap.py and `make verify-dev`); here we
pin the set-comparison that must FAIL on a partial migration run — the exact
case a naive "tracking table is non-empty" check would pass.
"""
from __future__ import annotations

import importlib.util
import re
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


# --------------------------------------------------------------------------- #
# Schema-USAGE grant matrix (issue #333) — pure helpers.
# --------------------------------------------------------------------------- #

def test_grant_matrix_parses_schema_grants_sql():
    """The expected matrix is parsed from the single-source schema_grants.sql."""
    pairs = check_health.load_grant_matrix()
    assert ("storage", "bloom_agent") in pairs
    assert ("auth", "bloom_writer") in pairs
    # #341 intentional gap: no auth USAGE for user/admin/agent.
    assert ("auth", "bloom_user") not in pairs
    assert ("auth", "bloom_agent") not in pairs


def test_schema_usage_problem_when_a_role_is_missing():
    expected = {
        ("storage", "bloom_user"),
        ("storage", "bloom_admin"),
        ("storage", "bloom_agent"),
        ("storage", "bloom_writer"),
    }
    observed = expected - {("storage", "bloom_agent")}  # partial: 3 of 4
    problems = check_health.schema_usage_problems(expected, observed)
    assert problems == ["role bloom_agent is missing USAGE on schema storage"]


def test_schema_usage_no_problem_when_complete():
    expected = {("storage", "bloom_agent"), ("auth", "bloom_writer")}
    assert check_health.schema_usage_problems(expected, expected) == []


def test_exited_core_service_with_nonzero_code_is_a_failure():
    """A crashed core service (e.g. realtime dying ~30s in on a bad DB_ENC_KEY)
    reports State=exited with a non-zero ExitCode and an EMPTY Health field (a
    dead container has no healthcheck result). That must still be a problem."""
    rows = [{"Service": "realtime", "Health": "", "State": "exited", "ExitCode": 1}]
    problems, warnings = check_health._classify_service_rows(rows)
    assert any("realtime" in p and "exited" in p for p in problems), problems
    assert not warnings


def test_exited_oneshot_service_with_zero_code_is_not_a_failure():
    """A one-shot init container (e.g. minio-init) legitimately exits 0 — a
    completed job, not a failure."""
    rows = [{"Service": "minio-init", "Health": "", "State": "exited", "ExitCode": 0}]
    problems, warnings = check_health._classify_service_rows(rows)
    assert problems == [] and warnings == []


def test_starting_required_service_is_settling():
    """A required service still in `starting` means 'keep waiting', not 'fail'."""
    rows = [
        {"Service": "bloommcp", "Health": "starting", "State": "running"},
        {"Service": "db-dev", "Health": "healthy", "State": "running"},
    ]
    assert check_health._services_still_settling(rows) == ["bloommcp"]


def test_optional_starting_service_is_not_settling():
    """An optional service (langchain-agent) starting must not hold up the check."""
    rows = [{"Service": "langchain-agent", "Health": "starting", "State": "running"}]
    assert check_health._services_still_settling(rows) == []


def test_all_healthy_means_settled():
    rows = [
        {"Service": "db-dev", "Health": "healthy", "State": "running"},
        {"Service": "bloommcp", "Health": "healthy", "State": "running"},
    ]
    assert check_health._services_still_settling(rows) == []


def test_parse_env_ignores_comments_and_blanks():
    text = "# comment\n\nPOSTGRES_USER=supabase_admin\nPOSTGRES_HOST_PORT=5433\n"
    env = check_health.parse_env(text)
    assert env == {"POSTGRES_USER": "supabase_admin", "POSTGRES_HOST_PORT": "5433"}


# --------------------------------------------------------------------------- #
# Id sequences behind their data (bloom#1022, fix-prod-sequences-behind §3)
# --------------------------------------------------------------------------- #

SEQUENCES_BEHIND_SQL = REPO_ROOT / "scripts" / "sql" / "sequences_behind.sql"


def test_sequence_problems_names_each_behind_sequence():
    rows = [("cyl_scanners", "id", 1, 1), ("species", "id", 16, 2)]
    problems = check_health.sequence_problems(rows)
    assert len(problems) == 2
    assert "public.cyl_scanners.id" in problems[0]
    assert "max 1" in problems[0] and "next value 1" in problems[0]
    assert "public.species.id" in problems[1]
    assert "max 16" in problems[1] and "next value 2" in problems[1]


def test_no_behind_sequences_means_no_problems():
    assert check_health.sequence_problems([]) == []


def test_main_fails_when_a_sequence_is_behind(monkeypatch):
    class _Conn:
        def close(self):
            pass

    monkeypatch.setattr(check_health, "_connect", lambda: _Conn())
    monkeypatch.setattr(check_health, "wait_for_auth_uid", lambda conn: True)
    for name in ("check_roles", "check_schemas", "check_migrations", "check_schema_usage"):
        monkeypatch.setattr(check_health, name, lambda conn: [])
    called = []

    def fake_check_sequences(conn):
        called.append(conn)
        return ["id sequence behind its data: public.t.id"]

    monkeypatch.setattr(check_health, "check_sequences", fake_check_sequences)
    assert check_health.main(["--skip-services"]) == 1
    assert called, "main() must run check_sequences"


def _sql_code(text: str) -> str:
    """Comments dropped and string literals blanked."""
    code = "\n".join(line.split("--", 1)[0] for line in text.splitlines())
    return re.sub(r"'(?:[^']|'')*'", "''", code)


def test_sequences_behind_sql_is_a_single_read_only_select():
    code = _sql_code(SEQUENCES_BEHIND_SQL.read_text(encoding="utf-8")).strip()
    assert code.upper().startswith("SELECT"), code[:40]
    assert code.count(";") == 1 and code.endswith(";")
    forbidden = r"\b(CREATE|DO|FUNCTION|INSERT|UPDATE|DELETE|ALTER|DROP|TRUNCATE|setval|nextval)\b"
    assert not re.search(forbidden, code, re.IGNORECASE)
    assert "pg_sequences" not in code, "pg_sequences.last_value is NULL until first call"


def test_check_health_and_deploy_reference_the_sql_file_not_a_copy():
    script = (REPO_ROOT / "scripts" / "check_health.py").read_text(encoding="utf-8")
    deploy = (REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    assert "sequences_behind.sql" in script
    assert deploy.count("< scripts/sql/sequences_behind.sql") == 2
    assert "query_to_xml" not in script and "query_to_xml" not in deploy
