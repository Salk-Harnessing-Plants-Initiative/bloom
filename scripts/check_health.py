#!/usr/bin/env python3
"""Verify the local dev stack is correct (issue #104).

``make check`` runs this. It asserts:

- every Compose service that defines a healthcheck reports ``healthy`` (and none
  has exited non-zero),
- the required Postgres roles exist (base Supabase roles + the bloom_* app roles),
- the ``auth`` and ``storage`` schemas exist (``auth.uid()``, ``storage.buckets``),
- every ``bloom_*`` role holds its expected schema ``USAGE`` (parsed from
  ``supabase/grants/schema_grants.sql``, the single source of truth — issue #333;
  a silent grant no-op fails here, loudly),
- every ``supabase/migrations/*.sql`` is recorded in
  ``supabase_migrations.schema_migrations`` — by **set comparison** (no missing,
  no orphan), not a brittle count.

The migration set-comparison mirrors
``tests/integration/test_migrations.py::test_all_migrations_recorded``.

Run via:  make check
   or:    uv run --with "psycopg[binary]" python scripts/check_health.py
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "supabase" / "migrations"
COMPOSE_FILE = REPO_ROOT / "docker-compose.dev.yml"
ENV_DEV = REPO_ROOT / ".env.dev"
# Single source of truth for the bloom_* schema-USAGE grants (issue #333):
# the same .sql file applied (as supabase_admin) by `make migrate-local`, CI, and
# the prod/staging manual step. check_schema_usage parses it for the expected set.
GRANT_FILE = REPO_ROOT / "supabase" / "grants" / "schema_grants.sql"
_GRANT_RE = re.compile(
    r"GRANT\s+USAGE\s+ON\s+SCHEMA\s+(\w+)\s+TO\s+([^;]+);",
    re.IGNORECASE,
)

# Roles the supabase/postgres image creates at init.
REQUIRED_BASE_ROLES = [
    "postgres",
    "anon",
    "authenticated",
    "service_role",
    "authenticator",
    "supabase_admin",
    "supabase_auth_admin",
    "supabase_storage_admin",
    "pgbouncer",
    "supabase_functions_admin",
]
# Application roles created by migrations (bloom_role_rls_policies etc.).
REQUIRED_APP_ROLES = ["bloom_admin", "bloom_user", "bloom_writer", "bloom_agent"]


# --------------------------------------------------------------------------- #
# Pure helpers (no DB / no docker) — unit-testable.
# --------------------------------------------------------------------------- #

def migration_file_versions(migrations_dir: Path = MIGRATIONS_DIR) -> set[str]:
    """The 14-digit version prefix of every migration file on disk."""
    files = glob.glob(str(Path(migrations_dir) / "*.sql"))
    return {Path(f).name.split("_", 1)[0] for f in files}


def migration_problems(
    file_versions: set[str], recorded_versions: set[str]
) -> list[str]:
    """Set comparison: report pending (on disk, not applied) and orphan rows.

    A count-equality check passes trivially when missing+extra rows cancel; a
    "merely non-empty tracking table" check passes on a partial run. Both are
    rejected here.
    """
    problems: list[str] = []
    pending = file_versions - recorded_versions
    orphans = recorded_versions - file_versions
    if pending:
        problems.append(
            f"{len(pending)} migration(s) on disk are NOT applied "
            f"(pending): {sorted(pending)[:5]}{'...' if len(pending) > 5 else ''}"
        )
    if orphans:
        problems.append(
            f"{len(orphans)} recorded migration(s) have no file on disk "
            f"(orphan): {sorted(orphans)[:5]}{'...' if len(orphans) > 5 else ''}"
        )
    return problems


def load_grant_matrix(path: Path = GRANT_FILE) -> set[tuple[str, str]]:
    """Parse the expected (schema, role) USAGE pairs from schema_grants.sql.

    The .sql file is the single source of truth; we read the grant set straight from
    it rather than keep a second machine-readable copy.
    """
    text = Path(path).read_text(encoding="utf-8")
    pairs: set[tuple[str, str]] = set()
    for schema, roles in _GRANT_RE.findall(text):
        for role in roles.split(","):
            pairs.add((schema, role.strip()))
    return pairs


def schema_usage_problems(
    expected: set[tuple[str, str]], observed: set[tuple[str, str]]
) -> list[str]:
    """Expected (schema, role) USAGE pairs that are missing from observed.

    A silent grant no-op (the #333 bug) shows up here as a missing pair even though
    the migration "succeeded" — reported per role/schema, including a partial run
    (e.g. 3 of 4 roles granted → the 4th is reported).
    """
    problems: list[str] = []
    for schema, role in sorted(expected - observed):
        problems.append(f"role {role} is missing USAGE on schema {schema}")
    return problems


def parse_env(text: str) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


# --------------------------------------------------------------------------- #
# DB checks (take a live psycopg connection).
# --------------------------------------------------------------------------- #

def _missing_roles(conn, roles: list[str]) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT rolname FROM pg_roles")
        present = {r[0] for r in cur.fetchall()}
    return [r for r in roles if r not in present]


def check_roles(conn) -> list[str]:
    problems = []
    missing_base = _missing_roles(conn, REQUIRED_BASE_ROLES)
    missing_app = _missing_roles(conn, REQUIRED_APP_ROLES)
    if missing_base:
        problems.append(f"missing base roles: {missing_base}")
    if missing_app:
        problems.append(f"missing application roles (migrations not applied?): {missing_app}")
    return problems


def _scalar(conn, sql: str):
    conn.rollback()  # fresh snapshot each probe
    with conn.cursor() as cur:
        cur.execute(sql)
        row = cur.fetchone()
        return row[0] if row else None


def check_schemas(conn) -> list[str]:
    problems = []
    if not _scalar(conn, "SELECT 1 FROM information_schema.schemata WHERE schema_name='auth'"):
        problems.append("auth schema missing")
    if not _scalar(
        conn,
        "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
        "WHERE n.nspname='auth' AND p.proname='uid'",
    ):
        problems.append("auth.uid() missing (GoTrue not initialised?)")
    if not _scalar(conn, "SELECT 1 FROM information_schema.schemata WHERE schema_name='storage'"):
        problems.append("storage schema missing")
    if not _scalar(conn, "SELECT to_regclass('storage.buckets')"):
        problems.append("storage.buckets missing (storage-api not initialised?)")
    return problems


def check_schema_usage(conn, matrix_path: Path = GRANT_FILE) -> list[str]:
    """Assert each bloom_* role holds its expected schema USAGE (issue #333).

    Catches a silent grant no-op (e.g. schema_grants.sql never applied, or applied as
    the wrong role) and an absent role/schema (reported, not crashed).
    """
    problems: list[str] = []

    # Expected schema-USAGE grants from the single-source .sql file.
    try:
        expected = load_grant_matrix(matrix_path)
    except Exception as exc:  # noqa: BLE001 — surface a bad/missing grants file
        problems.append(f"could not load grant set {matrix_path}: {exc}")
        return problems

    observed: set[tuple[str, str]] = set()
    with conn.cursor() as cur:
        for schema, role in expected:
            role_exists = _exists(cur, "SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
            schema_exists = _exists(
                cur,
                "SELECT 1 FROM information_schema.schemata WHERE schema_name = %s",
                (schema,),
            )
            if not role_exists:
                problems.append(f"role {role} does not exist (cannot check {schema} USAGE)")
                continue
            if not schema_exists:
                problems.append(f"schema {schema} does not exist (cannot check {role} USAGE)")
                continue
            cur.execute("SELECT has_schema_privilege(%s, %s, 'USAGE')", (role, schema))
            row = cur.fetchone()
            if row and row[0]:
                observed.add((schema, role))

    problems.extend(schema_usage_problems(expected, observed))
    return problems


def _exists(cur, sql: str, params: tuple) -> bool:
    cur.execute(sql, params)
    return cur.fetchone() is not None


def wait_for_auth_uid(conn, timeout: float = 60.0, interval: float = 2.0) -> bool:
    """Bounded poll for auth.uid() — CI only waits for storage.buckets, and
    GoTrue creates auth.uid() asynchronously, so probe instead of racing."""
    deadline = time.monotonic() + timeout
    while True:
        if _scalar(
            conn,
            "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
            "WHERE n.nspname='auth' AND p.proname='uid'",
        ):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def check_migrations(conn, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    if not _scalar(
        conn,
        "SELECT 1 FROM information_schema.tables WHERE table_schema='supabase_migrations' "
        "AND table_name='schema_migrations'",
    ):
        return ["supabase_migrations.schema_migrations missing (migrate-local not run)"]
    conn.rollback()
    with conn.cursor() as cur:
        cur.execute("SELECT version FROM supabase_migrations.schema_migrations")
        recorded = {r[0] for r in cur.fetchall()}
    return migration_problems(migration_file_versions(migrations_dir), recorded)


# --------------------------------------------------------------------------- #
# Service checks (docker compose) — local only.
# --------------------------------------------------------------------------- #

# Services that require user-supplied config to become healthy and are NOT part
# of the core dev substrate, so an unhealthy one is a warning, not a failure:
#   - langchain-agent needs LOCAL_LLM_URL / OPENAI_API_KEY (it builds a model at
#     import; with no LLM configured it can't start).
OPTIONAL_SERVICES = {"langchain-agent"}


def _classify_service_rows(rows: list[dict]) -> tuple[list[str], list[str]]:
    """Split service-health issues into hard problems vs optional warnings.

    Pure (no docker) so it is unit-testable. A service in OPTIONAL_SERVICES that
    is unhealthy/exited yields a warning; any other does a problem.
    """
    problems: list[str] = []
    warnings: list[str] = []
    for svc in rows:
        name = svc.get("Service") or svc.get("Name", "?")
        health = (svc.get("Health") or "").lower()
        state = (svc.get("State") or "").lower()
        issue = None
        if health and health not in ("healthy", ""):
            issue = f"service {name} health={health}"
        elif state == "exited" and str(svc.get("ExitCode", "0")) not in ("0", "None"):
            issue = f"service {name} exited (code {svc.get('ExitCode')})"
        if not issue:
            continue
        if name in OPTIONAL_SERVICES:
            warnings.append(
                f"{issue} (optional — set LOCAL_LLM_URL / OPENAI_API_KEY to enable it)"
            )
        else:
            problems.append(issue)
    return problems, warnings


def _services_still_settling(rows: list[dict]) -> list[str]:
    """Required (non-optional) services whose healthcheck is still 'starting'.

    Healthchecks first fire ~30s after start (e.g. bloommcp/realtime), so a
    `make check` run right after `make dev-up` can catch them mid-`starting`.
    Treat that as 'keep waiting', not a failure.
    """
    out = []
    for svc in rows:
        name = svc.get("Service") or svc.get("Name", "?")
        if name in OPTIONAL_SERVICES:
            continue
        if (svc.get("Health") or "").lower() == "starting":
            out.append(name)
    return out


def _compose_ps_rows() -> tuple[list[dict] | None, list[str]]:
    """Query `docker compose ps`. Return (rows, problems); rows is None on error."""
    try:
        out = subprocess.run(
            [
                "docker", "compose", "-f", str(COMPOSE_FILE),
                "--env-file", str(ENV_DEV), "ps", "--format", "json",
            ],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return None, [f"could not query docker compose: {exc}"]
    if out.returncode != 0:
        return None, [f"`docker compose ps` failed: {out.stderr.strip()[:200]}"]
    raw = out.stdout.strip()
    if not raw:
        return None, ["no compose services are running (did you `make dev-up`?)"]
    # Newer compose prints one JSON object per line; older prints a JSON array.
    if raw.startswith("["):
        return json.loads(raw), []
    return [json.loads(line) for line in raw.splitlines() if line.strip()], []


def check_services_healthy(
    timeout: float = 90.0, interval: float = 3.0
) -> tuple[list[str], list[str]]:
    """Return (problems, warnings) for compose service health, after bounded-
    waiting for required services to leave 'starting'. Optional LLM services that
    are down are warnings, not problems."""
    deadline = time.monotonic() + timeout
    while True:
        rows, errors = _compose_ps_rows()
        if rows is None:
            return errors, []
        if not _services_still_settling(rows) or time.monotonic() >= deadline:
            return _classify_service_rows(rows)
        time.sleep(interval)


# --------------------------------------------------------------------------- #
# Connection + main.
# --------------------------------------------------------------------------- #

def _connect():
    import psycopg  # imported here so pure helpers/unit tests don't need it

    env = parse_env(ENV_DEV.read_text(encoding="utf-8")) if ENV_DEV.exists() else {}
    user = env.get("POSTGRES_USER", "supabase_admin")
    password = env.get("POSTGRES_PASSWORD", "")
    db = env.get("POSTGRES_DB", "postgres")
    port = env.get("POSTGRES_HOST_PORT", "5432")
    conninfo = f"host=127.0.0.1 port={port} dbname={db} user={user} password={password}"
    return psycopg.connect(conninfo)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the local dev stack.")
    parser.add_argument(
        "--skip-services",
        action="store_true",
        help="skip the docker compose health check (DB checks only)",
    )
    args = parser.parse_args(argv)

    all_problems: list[str] = []
    all_warnings: list[str] = []

    if not args.skip_services:
        svc_problems, svc_warnings = check_services_healthy()
        all_problems += [f"[services] {p}" for p in svc_problems]
        all_warnings += [f"[services] {w}" for w in svc_warnings]

    try:
        conn = _connect()
    except Exception as exc:  # noqa: BLE001 — surface any connection failure
        all_problems.append(f"[db] could not connect to Postgres: {exc}")
        _report(all_problems, all_warnings)
        return 1

    try:
        if not wait_for_auth_uid(conn):
            all_problems.append("[db] auth.uid() never appeared within 60s")
        all_problems += [f"[db] {p}" for p in check_roles(conn)]
        all_problems += [f"[db] {p}" for p in check_schemas(conn)]
        all_problems += [f"[db] {p}" for p in check_migrations(conn)]
        all_problems += [f"[db] {p}" for p in check_schema_usage(conn)]
    finally:
        conn.close()

    _report(all_problems, all_warnings)
    return 1 if all_problems else 0


def _report(problems: list[str], warnings: list[str] | None = None) -> None:
    for w in warnings or []:
        print(f"  ⚠ {w}")
    if problems:
        print("Local dev stack is NOT healthy:")
        for p in problems:
            print(f"  ✗ {p}")
    else:
        print("Local dev stack is healthy: services up, roles + auth/storage "
              "schemas present, all migrations applied.")
        if warnings:
            print("(optional services are down — see warnings above)")


if __name__ == "__main__":
    raise SystemExit(main())
