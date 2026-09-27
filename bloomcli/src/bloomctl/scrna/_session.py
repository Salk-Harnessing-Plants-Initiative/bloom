"""The session the scrna commands act as: a signed-in client, the storage endpoint, and the role."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

from ._transfer import Endpoint

WRITE_ROLES = ("bloom_writer", "bloom_admin")


@dataclass(frozen=True)
class Connection:
    client: Any
    endpoint: Endpoint
    role: str


def claims_of(token: str) -> dict[str, Any]:
    """A JWT's claims, read without verifying it; the server verifies every request."""
    payload = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def connect(profile: str) -> Connection:
    from ..cli import _authed_client, _load_creds

    creds = _load_creds(profile)
    client = _authed_client(profile)
    token = client.auth.get_session().access_token
    return Connection(
        client=client,
        endpoint=Endpoint(creds.api_url, creds.anon_key, token),
        role=claims_of(token).get("role", ""),
    )
