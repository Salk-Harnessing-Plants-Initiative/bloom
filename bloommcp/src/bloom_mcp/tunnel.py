"""Admits people who reach bloommcp through the Salk connector's tunnel.

AIRC signs people in with Salk and forwards their requests through a Cloudflare
tunnel to Caddy's tunnel entrance, which requires a bearer token and sets
``X-Bloom-Tunnel`` to ``BLOOMMCP_TUNNEL_SECRET``. Only a request carrying that
secret is a tunnel request.

A tunnel request's bearer token must be a JWT signed by AIRC (ES256, keys from
``BLOOMMCP_TUNNEL_JWKS_URL``) for this environment's issuer and audience. Its
``email`` claim must be one ``@salk.edu`` address that belongs to an active Bloom
account (``public.bloom_user_for_email``, called as ``bloom_agent``). The request
is then admitted with the shared API key in place of the token, and its usage is
recorded under the account's id. Queries still run as ``bloom_agent``.
"""

from __future__ import annotations

import functools
import hmac
import json
import logging
import os
import threading
import time
from collections import OrderedDict

import anyio
import jwt

logger = logging.getLogger(__name__)

SECRET_ENV = "BLOOMMCP_TUNNEL_SECRET"
MIN_SECRET_LENGTH = 32
TUNNEL_HEADER = b"x-bloom-tunnel"
AUTHORIZATION_HEADER = b"authorization"
JWKS_URL_ENV = "BLOOMMCP_TUNNEL_JWKS_URL"
# A local keys file instead of the URL, for CI and tests (no network).
JWKS_FILE_ENV = "BLOOMMCP_TUNNEL_JWKS_FILE"
ISSUER_ENV = "BLOOMMCP_TUNNEL_JWT_ISSUER"
AUDIENCE_ENV = "BLOOMMCP_TUNNEL_JWT_AUDIENCE"
JWT_ALGORITHMS = ["ES256"]
EMAIL_CLAIM = "email"
# How long AIRC's public keys are cached, and the clock skew allowed on expiry.
JWKS_CACHE_SECONDS = 300
# AIRC's Cloudflare refuses Python's default User-Agent; a hung fetch waits at most this long.
JWKS_USER_AGENT = "bloommcp"
JWKS_FETCH_TIMEOUT_SECONDS = 5
JWT_LEEWAY_SECONDS = 30
NOT_VERIFIED_MESSAGE = (
    "Your Salk sign-in could not be verified; try reconnecting the Bloom connector."
)
SALK_DOMAIN = "@salk.edu"
MAX_EMAIL_LENGTH = 254
LOOKUP_RPC = "bloom_user_for_email"
CACHE_TTL_SECONDS = 60
CACHE_MAX_ENTRIES = 1024
# JSON-RPC server-error code for a refused tunnel request; `data.status` holds the HTTP status.
# Not -32001, which the TypeScript MCP SDK uses for a request timeout.
REFUSAL_CODE = -32003
# Larger POST bodies are not read for a request id; they get the plain JSON refusal.
MAX_REFUSAL_BODY_BYTES = 1024 * 1024
# A refused request's body not read within this many seconds gets the plain JSON refusal.
REFUSAL_BODY_READ_SECONDS = 10
# Longest error description written to the log for a failed lookup.
MAX_LOGGED_ERROR_CHARS = 300

# Printable ASCII without space: rules out whitespace and control characters.
_FIRST_ALLOWED, _LAST_ALLOWED = 0x21, 0x7E

_now = time.monotonic
_cache: OrderedDict[str, tuple[str, float]] = OrderedDict()
_cache_lock = threading.Lock()


class TunnelRefusal(Exception):
    """A tunnel request that must be refused with ``status`` and ``message``."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def jsonrpc_refusal(body: bytes, refusal: TunnelRefusal) -> bytes | None:
    """A JSON-RPC error carrying ``refusal`` for a body holding one request; None otherwise."""
    try:
        request = json.loads(body)
    except (ValueError, RecursionError):
        return None
    if not isinstance(request, dict) or not isinstance(request.get("method"), str):
        return None
    request_id = request.get("id")
    # A notification has no id; a bool is not a valid one.
    if not isinstance(request_id, (str, int)) or isinstance(request_id, bool):
        return None
    error = {
        "code": REFUSAL_CODE,
        "message": refusal.message,
        "data": {"status": refusal.status},
    }
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "error": error}).encode(
        "utf-8"
    )


def validate_env() -> None:
    """Fail at startup on a tunnel secret that is too short or has no API key."""
    secret = os.environ.get(SECRET_ENV)
    if not secret:
        return
    if len(secret) < MIN_SECRET_LENGTH:
        raise RuntimeError(
            f"{SECRET_ENV} must be at least {MIN_SECRET_LENGTH} characters "
            "(generate one with `openssl rand -hex 32`)."
        )
    if not os.environ.get("BLOOMMCP_API_KEY"):
        raise RuntimeError(
            f"{SECRET_ENV} is set but BLOOMMCP_API_KEY is not; tunnel requests "
            "are admitted with the API key."
        )
    missing = [name for name in (ISSUER_ENV, AUDIENCE_ENV) if not os.environ.get(name)]
    if not os.environ.get(JWKS_URL_ENV) and not os.environ.get(JWKS_FILE_ENV):
        missing.insert(0, JWKS_URL_ENV)
    if missing:
        raise RuntimeError(
            f"{SECRET_ENV} is set but {', '.join(missing)} is not; tunnel requests "
            "carry AIRC's JWT."
        )


def is_tunnel_request(headers: list[tuple[bytes, bytes]]) -> bool:
    """Does the request carry exactly one ``X-Bloom-Tunnel`` equal to the secret?"""
    secret = os.environ.get(SECRET_ENV)
    if not secret:
        return False
    values = [v for k, v in headers if k == TUNNEL_HEADER]
    if not values:
        return False
    if len(values) != 1 or not hmac.compare_digest(values[0], secret.encode()):
        logger.warning(
            "X-Bloom-Tunnel present but not valid; handled as a normal request"
        )
        return False
    return True


def _is_salk_address(value: str) -> bool:
    if not 0 < len(value) <= MAX_EMAIL_LENGTH:
        return False
    if any(not _FIRST_ALLOWED <= ord(c) <= _LAST_ALLOWED for c in value):
        return False
    if value.count("@") != 1:
        return False
    lowered = value.lower()
    return lowered.endswith(SALK_DOMAIN) and len(lowered) > len(SALK_DOMAIN)


def _bearer_token(headers: list[tuple[bytes, bytes]]) -> str:
    values = [v for k, v in headers if k == AUTHORIZATION_HEADER]
    if len(values) != 1:
        logger.warning("tunnel refused: %d Authorization headers", len(values))
        raise TunnelRefusal(403, NOT_VERIFIED_MESSAGE)
    scheme, _, token = values[0].decode("latin-1").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        logger.warning("tunnel refused: Authorization is not a bearer token")
        raise TunnelRefusal(403, NOT_VERIFIED_MESSAGE)
    return token.strip()


@functools.lru_cache(maxsize=4)
def _jwks_client(url: str) -> jwt.PyJWKClient:
    # The key set is cached for JWKS_CACHE_SECONDS; no per-key cache, so a removed key stops working.
    return jwt.PyJWKClient(
        url,
        cache_keys=False,
        lifespan=JWKS_CACHE_SECONDS,
        headers={"User-Agent": JWKS_USER_AGENT},
        timeout=JWKS_FETCH_TIMEOUT_SECONDS,
    )


@functools.lru_cache(maxsize=4)
def _jwks_from_file(path: str) -> jwt.PyJWKSet:
    with open(path, encoding="utf-8") as handle:
        return jwt.PyJWKSet.from_json(handle.read())


def _signing_key(token: str):
    path = os.environ.get(JWKS_FILE_ENV)
    if not path:
        return (
            _jwks_client(os.environ[JWKS_URL_ENV]).get_signing_key_from_jwt(token).key
        )
    kid = jwt.get_unverified_header(token).get("kid")
    for key in _jwks_from_file(path).keys:
        if key.key_id == kid:
            return key.key
    raise jwt.PyJWKClientError(f"no key with kid {kid!r}")


def _verified_email(token: str) -> str:
    """The ``email`` claim of AIRC's verified JWT, in lower case. Blocking: the key
    fetch is a network call."""
    try:
        key = _signing_key(token)
    except jwt.PyJWKClientConnectionError as exc:
        logger.error("tunnel: could not fetch AIRC's keys: %s", _describe_failure(exc))
        raise TunnelRefusal(
            503, "Could not verify your Salk sign-in; try again shortly."
        )
    except jwt.PyJWTError as exc:
        logger.warning("tunnel refused: token key not usable: %s", type(exc).__name__)
        raise TunnelRefusal(403, NOT_VERIFIED_MESSAGE)
    except Exception as exc:
        # A key set that isn't JSON, a dropped connection, an unreadable keys file.
        logger.error("tunnel: AIRC's keys not usable: %s", _describe_failure(exc))
        raise TunnelRefusal(
            503, "Could not verify your Salk sign-in; try again shortly."
        )
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=JWT_ALGORITHMS,
            audience=os.environ[AUDIENCE_ENV],
            issuer=os.environ[ISSUER_ENV],
            leeway=JWT_LEEWAY_SECONDS,
            options={"require": ["exp", "iss", "aud", EMAIL_CLAIM]},
        )
    except jwt.PyJWTError as exc:
        logger.warning("tunnel refused: token not valid: %s", type(exc).__name__)
        raise TunnelRefusal(403, NOT_VERIFIED_MESSAGE)
    email = claims[EMAIL_CLAIM]
    if not isinstance(email, str) or not _is_salk_address(email):
        logger.warning(
            "tunnel refused: email claim %r is not one @salk.edu address",
            str(email)[:MAX_EMAIL_LENGTH],
        )
        raise TunnelRefusal(403, "Your Salk sign-in did not include a @salk.edu email.")
    return email.lower()


async def salk_user(headers: list[tuple[bytes, bytes]]) -> str:
    """The ``@salk.edu`` address in AIRC's verified bearer token, in lower case."""
    token = _bearer_token(headers)
    return await anyio.to_thread.run_sync(_verified_email, token)


def _cached(email: str) -> str | None:
    with _cache_lock:
        entry = _cache.get(email)
        if entry is None:
            return None
        user_id, expires_at = entry
        if _now() >= expires_at:
            del _cache[email]
            return None
        return user_id


def _remember(email: str, user_id: str) -> None:
    with _cache_lock:
        _cache[email] = (user_id, _now() + CACHE_TTL_SECONDS)
        _cache.move_to_end(email)
        while len(_cache) > CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def cache_size() -> int:
    with _cache_lock:
        return len(_cache)


def _query(email: str) -> str | None:
    """The account id for ``email``, or None; raises on any other result."""
    from bloom_mcp import supabase_client
    from bloom_mcp.identity import is_valid_identity

    result = supabase_client.call_rpc(LOOKUP_RPC, {"p_email": email})
    if result is None:
        return None
    if isinstance(result, str) and is_valid_identity(result):
        return result.lower()
    raise ValueError(f"unexpected {LOOKUP_RPC} result type {type(result).__name__}")


def _describe_failure(exc: Exception) -> str:
    """The error's type plus PostgREST's code and message (or the error's text); no secrets."""
    code, message = getattr(exc, "code", None), getattr(exc, "message", None)
    detail = f"{code} {message}" if code or message else str(exc)
    return f"{type(exc).__name__}: {detail}"[:MAX_LOGGED_ERROR_CHARS]


def _no_account_message(email: str) -> str:
    site_url = (os.environ.get("SITE_URL") or "").rstrip("/")
    where = f" at {site_url}" if site_url else ""
    return f"No Bloom account for {email}. Create one{where}, or reach out to the Bloom team."


async def bloom_user(email: str) -> str:
    """The Bloom account id for ``email``, from the cache or the database."""
    cached = _cached(email)
    if cached is not None:
        return cached
    try:
        user_id = await anyio.to_thread.run_sync(_query, email)
    except Exception as exc:
        logger.error("tunnel lookup failed for %s: %s", email, _describe_failure(exc))
        raise TunnelRefusal(
            503, "Could not check your Bloom account; try again shortly."
        )
    if user_id is None:
        logger.warning("tunnel refused: no Bloom account for %s", email)
        raise TunnelRefusal(403, _no_account_message(email))
    logger.info("tunnel lookup: Bloom account found for %s", email)
    _remember(email, user_id)
    return user_id


def api_key() -> str:
    """The key FastMCP's ``ApiKeyVerifier`` holds."""
    from bloom_mcp import auth

    if not auth.API_KEY:
        logger.error("tunnel refused: BLOOMMCP_API_KEY is not set")
        raise TunnelRefusal(503, "The Bloom connector is not configured.")
    return auth.API_KEY


def with_api_key(scope: dict, key: str) -> dict:
    """``scope`` with its Authorization set to the API key and the secret removed."""
    dropped = (b"authorization", TUNNEL_HEADER)
    headers = [(k, v) for k, v in scope.get("headers") or [] if k not in dropped]
    headers.append((b"authorization", f"Bearer {key}".encode("latin-1")))
    return {**scope, "headers": headers}
