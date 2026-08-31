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
    # Both hold key material. JWT_JWKS looks like public keys but embeds
    # JWT_SECRET as a symmetric JWK so pre-migration tokens keep
    # verifying, so it is every bit as sensitive as the private half.
    "JWT_KEYS",
    "JWT_JWKS",
    "ANON_KEY",
    "SERVICE_ROLE_KEY",
    "DB_ENC_KEY",
    "MINIO_ROOT_PASSWORD",
    "MINIO_PASSWORD",
    "MINIO_ROOT_USER",
    "DASHBOARD_PASSWORD",
    "DASHBOARD_USERNAME",
    "BLOOMMCP_API_KEY",
    "VAULT_ENC_KEY",
    "SUPAVISOR_ENC_KEY",
    "SECRET_KEY_BASE",
    "OPENAI_API_KEY",
    "LANGCHAIN_API_KEY",
    "BLOOM_AGENT_KEY",
    "CLOUDFLARE_API_TOKEN",
    "DEPLOY_PATH",
    "MINIO_DATA_PATH",
    "WORKFLOWS_SUPABASE_EMAIL",
    "WORKFLOWS_SUPABASE_PASSWORD",
    # bloom #11 Phase 2 (cyl-pipeline-worker): real credentials for the
    # bloom-pipeline ServiceAccount. WORKFLOWS_K8S_NAMESPACE/_TTL_SECONDS are
    # deliberately NOT here — they're plain config values with safe code
    # defaults, sourced from .env.*.defaults instead (see
    # openspec/changes/add-cyl-pipeline-dispatch/design.md).
    "WORKFLOWS_K8S_TOKEN",
    "WORKFLOWS_K8S_CA_CERT",
    "WORKFLOWS_K8S_API_URL",
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
        # WORKFLOWS_K8S_ENV_LABEL's entire purpose (design.md's "fourth
        # environment label" decision) is disambiguating prod from staging in
        # the shared runai-busch-lab namespace — a copy-paste accident
        # collapsing them to the same value would silently defeat it with no
        # other test catching that.
        "WORKFLOWS_K8S_ENV_LABEL",
        # Same reasoning: prod and staging hold objects under identical
        # logical names, so one environment's mirror pointed at the other's
        # Box folder would overwrite real backups rather than sit beside them.
        "BACKUP_BOX_ROOT",
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


# --- Negative-path tests for scripts/validate_env.sh -----------------------
#
# These tests invoke the same validator script that deploy.yml calls, so the
# workflow and tests can never drift. The script is scripts/validate_env.sh.

VALIDATOR_SCRIPT = REPO_ROOT / "scripts" / "validate_env.sh"

# Minimal compose file that references just three vars, so scratch env
# files can be small enough to read in a glance.
_MINI_COMPOSE = """\
services:
  app:
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      SITE_URL: ${SITE_URL}
"""


def _run_validator(
    tmp_path: Path,
    env_content: str,
    with_eof_marker: bool = True,
) -> subprocess.CompletedProcess:
    env_file = tmp_path / ".env.test"
    if with_eof_marker:
        # Mirror what the deploy's heredoc does — the marker is the
        # last line and tells the validator the file is not truncated.
        if not env_content.endswith("\n"):
            env_content += "\n"
        env_content += "# _EOF_MARKER_\n"
    env_file.write_text(env_content)
    compose_file = tmp_path / "docker-compose.test.yml"
    compose_file.write_text(_MINI_COMPOSE)
    return subprocess.run(
        ["bash", str(VALIDATOR_SCRIPT), str(env_file), str(compose_file)],
        capture_output=True,
        text=True,
    )


def test_validator_rejects_missing_required_key(tmp_path):
    """Validator must fail when a key referenced by compose is absent from
    the env file. Guards against the old hardcoded-19-keys drift."""
    # POSTGRES_PASSWORD intentionally missing
    content = "POSTGRES_USER=admin\nSITE_URL=https://example.com\n"
    result = _run_validator(tmp_path, content)
    assert result.returncode == 1, f"expected exit 1, got {result.returncode}: {result.stderr}"
    assert "POSTGRES_PASSWORD" in result.stderr


def test_validator_rejects_whitespace_only_value(tmp_path):
    """Validator must reject KEY= with whitespace-only value. The old regex
    `=.+` accepted these silently (trailing-space paste accidents)."""
    content = (
        "POSTGRES_USER=admin\n"
        "POSTGRES_PASSWORD= \n"  # single trailing space — passes old regex, fails new
        "SITE_URL=https://example.com\n"
    )
    result = _run_validator(tmp_path, content)
    assert result.returncode == 1, f"expected exit 1, got {result.returncode}: {result.stderr}"
    assert "POSTGRES_PASSWORD" in result.stderr


def test_validator_rejects_comment_started_value(tmp_path):
    """Validator must reject KEY=#value. These look like forgotten
    placeholders (KEY=#paste-here-later) — the old regex treated them as
    legitimate values."""
    content = (
        "POSTGRES_USER=admin\n"
        "POSTGRES_PASSWORD=#todo\n"
        "SITE_URL=https://example.com\n"
    )
    result = _run_validator(tmp_path, content)
    assert result.returncode == 1, f"expected exit 1, got {result.returncode}: {result.stderr}"
    assert "POSTGRES_PASSWORD" in result.stderr


def test_validator_rejects_missing_eof_marker(tmp_path):
    """Validator must reject a file whose last line is not # _EOF_MARKER_.
    A missing marker indicates the heredoc write was interrupted
    (workflow cancel, SSH drop, SIGTERM from timeout-minutes) and the
    file is truncated — the rest of the validator would pass but
    containers would start with missing secrets."""
    # All required keys are present and well-formed — only the marker is
    # missing. with_eof_marker=False simulates a truncated write.
    content = (
        "POSTGRES_USER=admin\n"
        "POSTGRES_PASSWORD=realpassword\n"
        "SITE_URL=https://example.com\n"
    )
    result = _run_validator(tmp_path, content, with_eof_marker=False)
    assert result.returncode == 1, f"expected exit 1, got {result.returncode}: {result.stderr}"
    assert "EOF marker" in result.stderr or "Partial" in result.stderr


@pytest.mark.parametrize(
    "defaults_path,env_label",
    [(PROD_DEFAULTS, "prod"), (STAGING_DEFAULTS, "staging")],
)
def test_validator_accepts_real_defaults_plus_fake_secrets(
    tmp_path, defaults_path: Path, env_label: str
):
    """End-to-end: run the real committed defaults file through the real
    validator against the real compose file.

    The existing negative-path tests use a mini compose fixture — they can't
    catch the case where a committed default ships an empty value that the
    validator then rejects (the original CADDY_HTTP_PORT= regression, now
    historical: CADDY_HTTP_PORT was retired when the Caddyfile site address
    was replaced by the scheme-prefixed CADDY_SITE_ADDRESSES). This test
    assembles the file the way deploy.yml does — defaults installed,
    plausible secrets appended, EOF marker — and asserts the validator
    accepts it.
    """
    compose_text = COMPOSE_FILE.read_text()
    # Matches `${VAR}` and `${VAR:-default}` alike, as validate_env.sh does
    # (scripts/validate_env.sh:69). A narrower pattern here would skip
    # defaulted vars the validator still requires, so the fixture would
    # omit them and this test would fail for the wrong reason.
    referenced = set(re.findall(r"\$\{([A-Z_][A-Z0-9_]*)", compose_text))
    FILTERED = {"COMPOSE_PROJECT_NAME", "NEXT_PUBLIC_SUPABASE_COOKIE_NAME"}
    required = referenced - FILTERED

    defaults_text = defaults_path.read_text()
    defaults_keys = {
        line.split("=", 1)[0]
        for line in defaults_text.splitlines()
        if line and not line.startswith("#") and "=" in line
    }

    appended = "\n".join(
        f"{k}=fake-{k.lower()}" for k in sorted(required - defaults_keys)
    )
    content = defaults_text.rstrip("\n") + "\n" + appended + "\n"
    result = _run_validator_real_compose(tmp_path, content)
    assert result.returncode == 0, (
        f"validator rejected a file assembled from real {env_label} defaults"
        f" + fake secrets.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def _run_validator_real_compose(tmp_path: Path, env_content: str) -> subprocess.CompletedProcess:
    """Variant of _run_validator that uses the REAL docker-compose.prod.yml
    instead of a mini fixture. Always appends the EOF marker."""
    env_file = tmp_path / ".env.test"
    if not env_content.endswith("\n"):
        env_content += "\n"
    env_content += "# _EOF_MARKER_\n"
    env_file.write_text(env_content)
    return subprocess.run(
        ["bash", str(VALIDATOR_SCRIPT), str(env_file), str(COMPOSE_FILE)],
        capture_output=True,
        text=True,
    )


# --- Box object mirror (scheduled-jobs/box-object-backup) -------------------
#
# These are not ordinary config. An empty or wrong value does not fail the
# backup job loudly; it sends eight million images somewhere nobody is looking,
# and the run reports success while doing it. The job refuses to start on a bad
# value at runtime, and these keep a bad value from reaching the deploy at all.

BACKUP_REQUIRED_KEYS = (
    "BACKUP_BOX_REMOTE",
    "BACKUP_BOX_ROOT",
    "BACKUP_MINIO_BUCKET",
    "BACKUP_MINIO_PREFIX",
)


@pytest.mark.parametrize("key", BACKUP_REQUIRED_KEYS)
def test_backup_keys_are_present_and_non_empty(key):
    """An unset destination writes the mirror to the top of the Box drive.

    BACKUP_BOX_ROOT defaults to "" in the job, and an empty root makes every
    object land at `<bucket>/<name>` — the root of the Box account, alongside
    everyone else's folders. BACKUP_MINIO_BUCKET empty is the mirror image:
    rclone then reads each object's own bucket_id as a MinIO bucket name and
    every copy 404s.
    """
    for path in (PROD_DEFAULTS, STAGING_DEFAULTS):
        values = _parse(path)
        assert key in values, f"{path.name}: {key} is missing"
        assert values[key].strip(), f"{path.name}: {key} is empty"


def test_backup_box_root_names_its_own_environment():
    """Each root must sit under a segment naming its environment.

    Guards the copy-paste that points staging's mirror at prod's folder. The
    job makes the same check at runtime against --env; this catches it in the
    committed defaults, before it reaches a deploy.
    """
    for path, env, other in (
        (PROD_DEFAULTS, "prod", "staging"),
        (STAGING_DEFAULTS, "staging", "prod"),
    ):
        root = _parse(path)["BACKUP_BOX_ROOT"].strip().strip("/")
        segments = [s.lower() for s in root.split("/")]
        assert env in segments, (
            f"{path.name}: BACKUP_BOX_ROOT ({root!r}) has no {env!r} path "
            "segment, so it cannot be told apart from the other environment"
        )
        assert other not in segments, (
            f"{path.name}: BACKUP_BOX_ROOT ({root!r}) points into {other!r}"
        )


def test_backup_box_roots_are_not_nested_in_each_other():
    """Neither environment may mirror into a folder inside the other's."""
    prod = _parse(PROD_DEFAULTS)["BACKUP_BOX_ROOT"].strip().strip("/")
    staging = _parse(STAGING_DEFAULTS)["BACKUP_BOX_ROOT"].strip().strip("/")
    assert prod != staging, "prod and staging mirror into the same Box folder"
    assert not prod.startswith(staging + "/"), f"{prod!r} is inside {staging!r}"
    assert not staging.startswith(prod + "/"), f"{staging!r} is inside {prod!r}"


def test_backup_minio_bucket_matches_the_compose_backing_bucket():
    """The mirror must read from the bucket storage-api actually writes to.

    docker-compose.prod.yml sets STORAGE_S3_BUCKET for storage-api; if that
    ever changes and the backup's copy does not, the job reads an empty or
    wrong bucket and mirrors nothing while reporting success on zero objects.
    """
    compose = COMPOSE_FILE.read_text()
    match = re.search(r"^\s*STORAGE_S3_BUCKET:\s*(\S+)\s*$", compose, re.M)
    assert match, "STORAGE_S3_BUCKET not found in docker-compose.prod.yml"
    expected = match.group(1).strip().strip("\"'")
    for path in (PROD_DEFAULTS, STAGING_DEFAULTS):
        actual = _parse(path)["BACKUP_MINIO_BUCKET"].strip()
        assert actual == expected, (
            f"{path.name}: BACKUP_MINIO_BUCKET is {actual!r} but storage-api "
            f"writes to {expected!r} (docker-compose.prod.yml STORAGE_S3_BUCKET)"
        )


# The Box destination is pinned, not merely validated. It names a folder
# created empty for this job alone, and the job only ever uploads — it never
# lists the destination first, so pointing it at a folder that already holds
# something would interleave eight million objects into it with no complaint
# and no way to tell the two apart afterwards. The V1 archive
# (Bloom-Backups/Old_Bloom_Final_State) sits in the same account.
#
# A tripwire rather than a rule: changing the destination is a legitimate thing
# to want, and this does not prevent it. It makes it deliberate, by requiring
# the change to appear here too, where a reviewer sees it.
EXPECTED_BOX_ROOTS = {
    "prod": "Bloom-Backups/BloomV2-Data-Backup/prod/storage",
    "staging": "Bloom-Backups/BloomV2-Data-Backup/staging/storage",
}


@pytest.mark.parametrize("env,expected", sorted(EXPECTED_BOX_ROOTS.items()))
def test_backup_box_root_is_the_folder_this_job_was_given(env, expected):
    path = PROD_DEFAULTS if env == "prod" else STAGING_DEFAULTS
    actual = _parse(path)["BACKUP_BOX_ROOT"].strip()
    assert actual == expected, (
        f"{path.name}: BACKUP_BOX_ROOT is {actual!r}, expected {expected!r}.\n"
        "This job only uploads and never inspects the destination first, so a "
        "changed root silently mixes the mirror into whatever is already "
        "there. If the move is intended, update EXPECTED_BOX_ROOTS in this "
        "test so the change is visible in review."
    )


def test_both_environments_mirror_under_one_dedicated_parent():
    # Everything this job writes stays inside a folder created for it, rather
    # than being scattered across the Box account.
    parent = "Bloom-Backups/BloomV2-Data-Backup"
    for path in (PROD_DEFAULTS, STAGING_DEFAULTS):
        root = _parse(path)["BACKUP_BOX_ROOT"].strip()
        assert root.startswith(parent + "/"), (
            f"{path.name}: {root!r} is outside {parent!r}"
        )


def test_the_destination_is_not_the_v1_archive():
    # A specific folder in the same account holding the one-time V1 S3 archive.
    # Nothing distinguishes it from an empty folder at runtime.
    for path in (PROD_DEFAULTS, STAGING_DEFAULTS):
        root = _parse(path)["BACKUP_BOX_ROOT"].strip().lower()
        assert "old_bloom_final_state" not in root, (
            f"{path.name}: points at the V1 archive"
        )
