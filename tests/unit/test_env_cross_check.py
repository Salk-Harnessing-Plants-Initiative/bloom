"""Cross-environment divergence guards for the committed env defaults.

Enforces requirements from
openspec/changes/add-ghcr-image-publishing/specs/frontend-runtime-config/spec.md:
  - "Cross-Environment Configuration Fence — Cookie Name Divergence"
  - "Cross-Environment Configuration Fence — Distinct Supabase URLs"

The cross-environment fence (design.md Decision 13/14) only protects against a
misconfigured deploy if staging and prod actually DIFFER. Two values must
diverge between ``.env.staging.defaults`` and ``.env.prod.defaults``:

1. ``SUPABASE_COOKIE_NAME`` — if both environments share a cookie name, a
   researcher with staging + prod open in one browser gets colliding auth
   cookies; the staging session can clobber the prod session token (routing
   authenticated writes at the wrong Supabase project). Staging MUST use the
   ``sb-bloom-staging-auth-token`` suffix; prod retains ``sb-bloom-auth-token``.
2. ``NEXT_PUBLIC_SUPABASE_URL`` — the URL-host fence has nothing to fence
   against if both environments point the browser at the same Supabase URL.

Runs in the ``python-audit`` CI job (``uv run --extra test pytest tests/unit/``).
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
PROD_DEFAULTS = REPO_ROOT / ".env.prod.defaults"
STAGING_DEFAULTS = REPO_ROOT / ".env.staging.defaults"

EXPECTED_STAGING_COOKIE = "sb-bloom-staging-auth-token"
EXPECTED_PROD_COOKIE = "sb-bloom-auth-token"


def _read_env(path: Path) -> dict[str, str]:
    """Parse a committed ``.env.*.defaults`` file into a key/value dict.

    Ignores blank lines and ``#`` comments. Values are taken verbatim after the
    first ``=`` (no quote stripping — these files don't quote values).
    """
    env: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip()
    return env


def test_cookie_names_diverge_between_staging_and_prod() -> None:
    """Cookie-Name Divergence: staging and prod MUST NOT share a cookie name."""
    prod = _read_env(PROD_DEFAULTS)
    staging = _read_env(STAGING_DEFAULTS)
    prod_cookie = prod.get("SUPABASE_COOKIE_NAME")
    staging_cookie = staging.get("SUPABASE_COOKIE_NAME")
    assert prod_cookie is not None, ".env.prod.defaults must set SUPABASE_COOKIE_NAME"
    assert (
        staging_cookie is not None
    ), ".env.staging.defaults must set SUPABASE_COOKIE_NAME"
    assert staging_cookie != prod_cookie, (
        "staging and prod declare the SAME SUPABASE_COOKIE_NAME "
        f"({staging_cookie!r}); a researcher with both environments open in one "
        "browser would get colliding auth cookies. Staging must use a "
        "distinct (staging-suffixed) cookie name."
    )


def test_staging_declares_staging_suffixed_cookie_name() -> None:
    """Staging defaults MUST declare the staging-suffixed cookie name."""
    staging = _read_env(STAGING_DEFAULTS)
    assert staging.get("SUPABASE_COOKIE_NAME") == EXPECTED_STAGING_COOKIE, (
        f".env.staging.defaults SUPABASE_COOKIE_NAME must be "
        f"{EXPECTED_STAGING_COOKIE!r}, got "
        f"{staging.get('SUPABASE_COOKIE_NAME')!r}."
    )


def test_prod_retains_original_cookie_name() -> None:
    """Prod defaults MUST retain the original (unsuffixed) cookie name."""
    prod = _read_env(PROD_DEFAULTS)
    assert prod.get("SUPABASE_COOKIE_NAME") == EXPECTED_PROD_COOKIE, (
        f".env.prod.defaults SUPABASE_COOKIE_NAME must be "
        f"{EXPECTED_PROD_COOKIE!r}, got {prod.get('SUPABASE_COOKIE_NAME')!r}."
    )


def test_public_supabase_urls_diverge_between_staging_and_prod() -> None:
    """Distinct Supabase URLs: the URL fence needs the two URLs to differ."""
    prod = _read_env(PROD_DEFAULTS)
    staging = _read_env(STAGING_DEFAULTS)
    prod_url = prod.get("NEXT_PUBLIC_SUPABASE_URL")
    staging_url = staging.get("NEXT_PUBLIC_SUPABASE_URL")
    assert prod_url, ".env.prod.defaults must set NEXT_PUBLIC_SUPABASE_URL"
    assert staging_url, ".env.staging.defaults must set NEXT_PUBLIC_SUPABASE_URL"
    assert staging_url != prod_url, (
        "staging and prod declare the SAME NEXT_PUBLIC_SUPABASE_URL "
        f"({staging_url!r}); the cross-environment URL-host fence has nothing "
        "to fence against."
    )
