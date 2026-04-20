"""Unit tests for .env.prod.defaults and .env.staging.defaults.

Enforces the Committed Defaults contract from the deploy-env-config spec:
  openspec/changes/refactor-env-config-committed-defaults/specs/deploy-env-config/spec.md

These defaults files MUST NOT contain secrets, MUST share the same key set
between prod and staging, MUST NOT overlap with the sensitive inventory
that lives in GitHub Secrets, and MUST cover every env var referenced by
docker-compose.prod.yml.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
PROD_DEFAULTS = REPO_ROOT / ".env.prod.defaults"
STAGING_DEFAULTS = REPO_ROOT / ".env.staging.defaults"
COMPOSE_FILE = REPO_ROOT / "docker-compose.prod.yml"

# Secret names that MUST NOT appear in the defaults files. Any value here
# lives in GitHub Secrets, not in the committed config.
SENSITIVE_INVENTORY = {
    "POSTGRES_PASSWORD",
    "JWT_SECRET",
    "ANON_KEY",
    "SERVICE_ROLE_KEY",
    "DB_ENC_KEY",
    "MINIO_ROOT_PASSWORD",
    "MINIO_PASSWORD",
    "DASHBOARD_PASSWORD",
    "BLOOMMCP_API_KEY",
    "VAULT_ENC_KEY",
    "SUPAVISOR_ENC_KEY",
    "SECRET_KEY_BASE",
    "OPENAI_API_KEY",
    "LANGCHAIN_API_KEY",
    # Infrastructure-topology paths — also secret-bucket
    "DEPLOY_PATH",
    "MINIO_DATA_PATH",
}

# Patterns that indicate a secret value (not just a key name). Case-insensitive.
SECRET_VALUE_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]+-----"),
    re.compile(r"^sk-[A-Za-z0-9]{20,}$"),  # OpenAI-style
    re.compile(r"^eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+$"),  # JWT
]


def _parse(path: Path) -> dict[str, str]:
    """Parse a KEY=VALUE file into a dict. Ignore blank/comment lines."""
    result: dict[str, str] = {}
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            pytest.fail(f"{path.name}:{lineno}: malformed line (no =): {raw!r}")
        key, _, value = stripped.partition("=")
        result[key.strip()] = value.strip()
    return result


def test_defaults_files_exist_and_tracked_in_git():
    assert PROD_DEFAULTS.exists(), f"missing {PROD_DEFAULTS}"
    assert STAGING_DEFAULTS.exists(), f"missing {STAGING_DEFAULTS}"
    # git ls-files confirms tracked (not just on disk)
    tracked = subprocess.run(
        ["git", "ls-files", ".env.prod.defaults", ".env.staging.defaults"],
        capture_output=True, text=True, cwd=REPO_ROOT, check=True,
    ).stdout.splitlines()
    assert ".env.prod.defaults" in tracked
    assert ".env.staging.defaults" in tracked


def test_no_secret_patterns():
    for path in (PROD_DEFAULTS, STAGING_DEFAULTS):
        content = path.read_text()
        # Reject lines with sensitive key NAMES
        for key in SENSITIVE_INVENTORY:
            pattern = re.compile(rf"^{key}=", re.MULTILINE)
            matches = pattern.findall(content)
            assert not matches, (
                f"{path.name}: sensitive key {key!r} must not appear in defaults"
            )
        # Reject lines with secret-looking values
        for lineno, line in enumerate(content.splitlines(), start=1):
            if line.startswith("#") or not line.strip():
                continue
            for pat in SECRET_VALUE_PATTERNS:
                assert not pat.search(line), (
                    f"{path.name}:{lineno}: matches secret pattern {pat.pattern!r}: {line}"
                )


def test_no_crlf_line_endings():
    for path in (PROD_DEFAULTS, STAGING_DEFAULTS):
        raw_bytes = path.read_bytes()
        assert b"\r\n" not in raw_bytes, f"{path.name} has CRLF line endings; use LF"


def test_no_duplicate_keys_in_defaults():
    for path in (PROD_DEFAULTS, STAGING_DEFAULTS):
        seen: set[str] = set()
        for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key = stripped.partition("=")[0].strip()
            assert key not in seen, f"{path.name}:{lineno}: duplicate key {key}"
            seen.add(key)


def test_prod_staging_key_sets_are_identical():
    prod = set(_parse(PROD_DEFAULTS).keys())
    staging = set(_parse(STAGING_DEFAULTS).keys())
    only_prod = prod - staging
    only_staging = staging - prod
    assert not only_prod, f"keys only in prod: {sorted(only_prod)}"
    assert not only_staging, f"keys only in staging: {sorted(only_staging)}"


def test_env_disambiguating_values_differ():
    """prod and staging must differ on DOMAIN_MAIN, SITE_URL, and
    staging's ports, so a misrouted secret can't silently point staging at
    prod (or vice versa)."""
    prod = _parse(PROD_DEFAULTS)
    staging = _parse(STAGING_DEFAULTS)
    for key in (
        "DOMAIN_MAIN", "DOMAIN_STUDIO", "DOMAIN_MINIO",
        "SITE_URL", "API_EXTERNAL_URL", "NEXT_PUBLIC_SUPABASE_URL",
        "NEXT_PUBLIC_APP_URL", "SUPABASE_PUBLIC_URL",
        "STUDIO_SUPABASE_PUBLIC_URL", "MINIO_BROWSER_REDIRECT_URL",
        "CORS_ORIGINS", "BLOOM_PLOTS_URL",
        "CADDY_HTTP_LISTEN_PORT", "CADDY_HTTPS_LISTEN_PORT",
        "POSTGRES_HOST_PORT",
        "LANGCHAIN_PROJECT",
    ):
        assert prod[key] != staging[key], (
            f"{key} identical in prod/staging ({prod[key]!r}); "
            "environments must differ on user-facing URLs + host ports"
        )


def test_no_overlap_with_sensitive_inventory():
    """No key name in either defaults file may also appear in the
    sensitive inventory. Append order would determine the winner, which is
    never intentional."""
    prod = set(_parse(PROD_DEFAULTS).keys())
    staging = set(_parse(STAGING_DEFAULTS).keys())
    overlap_prod = prod & SENSITIVE_INVENTORY
    overlap_staging = staging & SENSITIVE_INVENTORY
    assert not overlap_prod, (
        f"prod defaults overlap with sensitive inventory: {sorted(overlap_prod)}"
    )
    assert not overlap_staging, (
        f"staging defaults overlap with sensitive inventory: {sorted(overlap_staging)}"
    )


def test_all_compose_vars_are_sourced():
    """Every ${VAR} referenced in docker-compose.prod.yml must be provided
    by defaults OR by the sensitive inventory. If compose references a var
    that neither source provides, deploy will start containers with empty
    values."""
    compose = COMPOSE_FILE.read_text()
    # Match ${VAR}, ${VAR:-default}, ${VAR-default}
    refs = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*)(?::?-[^}]*)?\}", compose))
    defaults = set(_parse(PROD_DEFAULTS).keys())
    # Some vars are compose-level substitutions for the default project
    # name that don't need env-file entries.
    compose_internals = {"COMPOSE_PROJECT_NAME"}
    # NEXT_PUBLIC_SUPABASE_COOKIE_NAME is set via SUPABASE_COOKIE_NAME in compose
    aliases = {"NEXT_PUBLIC_SUPABASE_COOKIE_NAME": "SUPABASE_COOKIE_NAME"}
    unresolved = set()
    for ref in refs:
        if ref in compose_internals:
            continue
        if ref in aliases and aliases[ref] in defaults | SENSITIVE_INVENTORY:
            continue
        if ref in defaults:
            continue
        if ref in SENSITIVE_INVENTORY:
            continue
        unresolved.add(ref)
    assert not unresolved, (
        f"docker-compose.prod.yml references vars with no source: {sorted(unresolved)}"
    )
