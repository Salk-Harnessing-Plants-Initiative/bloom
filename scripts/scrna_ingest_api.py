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

# Rows per read. Each page is one request that has to finish inside Kong's 60 s.
PAGE_ROWS = 5000

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


def claims_of(access_token: str) -> dict:
    """A JWT's claims, read without verifying it; the server verifies."""
    payload = access_token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def role_of(access_token: str) -> str:
    return claims_of(access_token).get("role", "")


class Session:
    """A signed-in client, who signed in, and the means to sign in again."""

    def __init__(self, attempt, client, role: str, user_id: str):
        self._attempt, self.client, self.role, self.user_id = attempt, client, role, user_id

    def renew(self) -> None:
        self.client, self.role, self.user_id = self._attempt()


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
        claims = claims_of(res.session.access_token)
        role = claims.get("role", "")
        if role not in WRITE_ROLES:
            raise IngestError(f"{email} signs in as {role}; loading needs a writer "
                              f"or admin account")
        return client, role, claims.get("sub", "")

    return Session(attempt, *attempt())


def _api_errors() -> tuple:
    """The exceptions a request can raise; anything else is a fault in the loader."""
    import httpx
    from postgrest.exceptions import APIError
    from storage3.exceptions import StorageApiError
    return (httpx.HTTPError, APIError, StorageApiError)


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

    def read(self, fetch):
        """Run fetch(client); a read may be repeated, after one fresh sign-in."""
        for attempt in (1, 2):
            try:
                return fetch(self.session.client)
            except _api_errors() as exc:
                if classify(exc) == "unauthorised" and attempt == 1:
                    self.session.renew()
                    continue
                raise IngestError(f"reading failed: {exc}; {RERUN}") from None

    def write(self, step: str, send):
        """Run send(client), one request, under the write rules."""
        for attempt in (1, 2):
            try:
                return send(self.session.client)
            except _api_errors() as exc:
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


def read_all(writer: Writer, table: str, columns: str, *, filters=(),
             order: str = "id", page: int = PAGE_ROWS) -> list[dict]:
    """Every matching row, one page per request, ordered by `order`.

    filters: (method, column, value), e.g. ("eq", "dataset_id", 7) or
    ("is_", "deleted_at", "null").
    """
    rows: list[dict] = []
    start = 0
    while True:
        def fetch(client, start=start):
            query = client.table(table).select(columns)
            for method, column, value in filters:
                query = getattr(query, method)(column, value)
            return query.order(order).range(start, start + page - 1).execute().data
        batch = writer.read(fetch)
        rows.extend(batch)
        if len(batch) < page:
            return rows
        start += page


def insert(writer: Writer, step: str, table: str, rows: list[dict], *,
           returning: bool = False) -> list[dict]:
    """Insert rows in one request, sent once."""
    def send(client):
        query = client.table(table).insert(
            rows, returning="representation" if returning else "minimal")
        return query.retry(False).execute().data
    return writer.write(step, send)


def update(writer: Writer, step: str, table: str, values: dict, *, eq: dict) -> None:
    """Update the rows matching every eq pair, in one request, sent once."""
    def send(client):
        query = client.table(table).update(values, returning="minimal")
        for column, value in eq.items():
            query = query.eq(column, value)
        return query.retry(False).execute().data
    writer.write(step, send)


# The annotation release some exports append to a gene name, e.g. AT1G01010.Araport11.447.
RELEASE_SUFFIX = re.compile(r"\.Araport11\.\d+$")


def strip_release(gene: str) -> str:
    return RELEASE_SUFFIX.sub("", gene)


# What a dataset name may hold, since the counts objects are stored under it.
SAFE_DATASET_NAME = re.compile(r"[A-Za-z0-9._ -]+")
DOTS_ONLY = re.compile(r"\.+")


def dataset_name_ok(name: str) -> bool:
    return bool(SAFE_DATASET_NAME.fullmatch(name)) and not DOTS_ONLY.fullmatch(name)


DATASET_COLUMNS = "id,name,deleted_at,source_checksum,ingested_at,metadata,n_cells"


def find_dataset(writer: Writer, species_id: int, name: str) -> dict | None:
    """Look a dataset up by trimmed name; PostgREST cannot filter on btrim(name)."""
    rows = read_all(writer, "scrna_datasets", DATASET_COLUMNS,
                    filters=[("eq", "species_id", species_id),
                             ("is_", "deleted_at", "null")])
    return pick_dataset(rows, name)
