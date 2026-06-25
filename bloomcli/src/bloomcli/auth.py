"""Auth helpers for bloomcli: anon-key bootstrap + login verification.

Heavy imports (httpx, supabase) are deferred into the functions that use them so
importing this module (e.g. for ``bloomcli --help``) stays fast.
"""

from __future__ import annotations

DEFAULT_SERVER = "https://bloom.salk.edu"


class AuthError(Exception):
    """Login bootstrap or authentication failed."""


def fetch_anon_credentials(
    server: str = DEFAULT_SERVER, *, timeout: float = 10.0
) -> tuple[str, str]:
    """Fetch ``(api_url, anon_key)`` from ``<server>/api/client-info``.

    Mirrors the legacy CLI bootstrap. Raises :class:`AuthError` if the endpoint
    is unreachable (e.g. a 401 while the Kong basic-auth gate still covers it),
    pointing the user at the ``--api-url`` / ``--anon-key`` overrides.
    """
    import httpx

    url = f"{server.rstrip('/')}/api/client-info"
    try:
        resp = httpx.get(url, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        return data["api_url"], data["anon_key"]
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise AuthError(
            f"could not fetch client config from {url}: {exc}. "
            "Pass --api-url and --anon-key to skip this fetch."
        ) from exc


def verify_credentials(api_url: str, anon_key: str, email: str, password: str) -> None:
    """Authenticate against Supabase; raise :class:`AuthError` on failure."""
    from supabase import create_client

    client = create_client(api_url, anon_key)
    try:
        res = client.auth.sign_in_with_password({"email": email, "password": password})
    except Exception as exc:  # supabase raises various AuthApiError subtypes
        raise AuthError(f"sign-in failed: {exc}") from exc
    if not getattr(res, "session", None):
        raise AuthError("sign-in failed — check email/password")


def make_authed_client(creds):
    """Create a Supabase client signed in as ``creds`` (for authenticated queries)."""
    from supabase import create_client

    client = create_client(creds.api_url, creds.anon_key)
    try:
        res = client.auth.sign_in_with_password(
            {"email": creds.email, "password": creds.password}
        )
    except Exception as exc:
        raise AuthError(f"sign-in failed: {exc}") from exc
    if not getattr(res, "session", None):
        raise AuthError(
            "sign-in failed — check stored credentials (try `bloomcli login`)"
        )
    return client
