#!/usr/bin/env python3
"""Sign-in and write rules shared by the single-cell loaders.

The loaders write through the site's API as a writer account, not straight to the
database. Each write is one request, sent once; the only re-send is a write refused
as unauthorised, which never ran.
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import time
import urllib.request
from pathlib import Path

# Roles allowed to load data.
WRITE_ROLES = ("bloom_writer", "bloom_admin")

# How long a write can keep running on the server after the client gave up: the
# statement timeout applying to the writer's requests, plus a margin.
STATEMENT_TIMEOUT_S = 300
WAIT_MARGIN_S = 30

RERUN = "re-run the same command to continue"

# PostgREST codes for a missing, invalid or expired token.
UNAUTHORISED_CODES = ("PGRST301", "PGRST303", 401, "401")


class IngestError(RuntimeError):
    """Something about the files, the account or the dataset makes this unsafe to write."""


def _http_get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=10) as resp:
        return resp.read()


def resolve_api(server: str | None, api_url: str | None, anon_key: str | None,
                *, get=_http_get) -> tuple[str, str]:
    """The API URL and anon key: given directly, or read from the site."""
    if api_url and anon_key:
        return api_url, anon_key
    if not server:
        raise IngestError("give --server, or both --api-url and --anon-key")
    url = f"{server.rstrip('/')}/api/client-info"
    try:
        data = json.loads(get(url))
        return data["api_url"], data["anon_key"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise IngestError(f"could not read the API address from {url} ({exc}); "
                          f"pass --api-url and --anon-key instead") from None


def read_password(env=None) -> str:
    password = (os.environ if env is None else env).get("BLOOM_PASSWORD")
    if not password:
        raise IngestError("the password is read from BLOOM_PASSWORD, which is not set")
    return password


def role_of(access_token: str) -> str:
    """The role claim of a JWT, read without verifying it; the server verifies."""
    payload = access_token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload)).get("role", "")


class Session:
    """A signed-in client, and the means to sign in again."""

    def __init__(self, attempt, client, role: str):
        self._attempt, self.client, self.role = attempt, client, role

    def renew(self) -> None:
        self.client, self.role = self._attempt()


def sign_in(api_url: str, anon_key: str, email: str, password: str,
            *, create_client=None) -> Session:
    """Sign in, and refuse an account that cannot write."""
    if create_client is None:
        from supabase import create_client
    from supabase_auth.errors import AuthError

    def attempt():
        client = create_client(api_url, anon_key)
        try:
            res = client.auth.sign_in_with_password({"email": email, "password": password})
        except (AuthError, OSError) as exc:
            raise IngestError(f"could not sign in as {email}: {exc}") from None
        if not getattr(res, "session", None):
            raise IngestError(f"could not sign in as {email}: no session returned")
        role = role_of(res.session.access_token)
        if role not in WRITE_ROLES:
            raise IngestError(f"{email} signs in as {role}; loading needs a writer "
                              f"or admin account")
        return client, role

    client, role = attempt()
    return Session(attempt, client, role)


def classify(exc: BaseException) -> str:
    """'unauthorised' (never ran), 'failed' (did not commit) or 'unknown' (may have)."""
    import httpx
    from postgrest.exceptions import APIError
    from storage3.exceptions import StorageApiError

    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return "failed"
    if isinstance(exc, httpx.HTTPError):
        return "unknown"
    if isinstance(exc, APIError):
        if exc.code in UNAUTHORISED_CODES:
            return "unauthorised"
        return "unknown" if isinstance(exc.code, int) and exc.code >= 500 else "failed"
    if isinstance(exc, StorageApiError):
        status = int(exc.status) if str(exc.status).isdigit() else 0
        if exc.code == "InvalidJWT" or status == 401:
            return "unauthorised"
        return "unknown" if status >= 500 else "failed"
    return "unknown"


class Marker:
    """When a write last failed with an unknown outcome, kept beside the input."""

    def __init__(self, path, wait_s: float = STATEMENT_TIMEOUT_S + WAIT_MARGIN_S,
                 clock=time.time):
        self.path, self.wait_s, self.clock = Path(path), wait_s, clock

    def record(self, step: str) -> None:
        self.path.write_text(json.dumps({"at": self.clock(), "step": step}))

    def check(self) -> None:
        """Refuse until a write that may still be running has certainly finished."""
        if not self.path.exists():
            return
        record = json.loads(self.path.read_text())
        left = record["at"] + self.wait_s - self.clock()
        if left > 0:
            raise IngestError(f"'{record['step']}' may still be finishing on the "
                              f"server; wait {math.ceil(left)} seconds, then run the "
                              f"same command again")
        self.path.unlink()


def marker_path(input_path, dataset_name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", dataset_name.strip())
    return Path(input_path).parent / f".scrna-unknown-write.{safe}.json"


class Writer:
    """Sends each write once; re-sends only after an unauthorised refusal."""

    def __init__(self, session: Session, marker: Marker):
        self.session, self.marker = session, marker

    def write(self, step: str, send):
        """Run send(client), one request, under the write rules."""
        for attempt in (1, 2):
            try:
                return send(self.session.client)
            except IngestError:
                raise
            except Exception as exc:
                kind = classify(exc)
                if kind == "unauthorised" and attempt == 1:
                    self.session.renew()
                    continue
                if kind == "unknown":
                    self.marker.record(step)
                    raise IngestError(f"{step}: {exc}. Its outcome is unknown; "
                                      f"{RERUN}") from None
                raise IngestError(f"{step}: {exc}; {RERUN}") from None


def pick_dataset(rows: list[dict], name: str) -> dict | None:
    """The live dataset whose trimmed name matches, if exactly one does."""
    wanted = name.strip()
    live = [r for r in rows
            if r.get("deleted_at") is None and (r.get("name") or "").strip() == wanted]
    if len(live) > 1:
        ids = ", ".join(str(r["id"]) for r in live)
        raise IngestError(f"{len(live)} datasets are named {wanted!r} (ids {ids}); "
                          f"cannot tell which to write to")
    return live[0] if live else None


def find_dataset(client, species_id: int, name: str) -> dict | None:
    """Look a dataset up by trimmed name; PostgREST cannot filter on btrim(name)."""
    rows = (client.table("scrna_datasets")
            .select("id,name,deleted_at,source_checksum,ingested_at")
            .eq("species_id", species_id).is_("deleted_at", "null")
            .execute().data)
    return pick_dataset(rows, name)
