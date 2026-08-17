"""Client for an `rclone rcd` daemon, plus the MinIO connection string.

Copying under the logical name means one rename per object — `copyto`, not
`copy` — and there are millions of them. Spawning an `rclone` process per
object would re-read the config, re-auth to Box, and open a fresh TLS
connection every time; a single long-lived daemon reuses the Box token and
its HTTP connections across the whole run, which matters far more than CPU
here because Box throttles on API calls.

The daemon runs in a container on `supanet` (MinIO's S3 port is never
published to the host), with its RC port bound to loopback only.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from base64 import b64encode
from dataclasses import dataclass

logger = logging.getLogger(__name__)

DEFAULT_RC_PORT = 5572
DEFAULT_TIMEOUT_SECONDS = 900  # a single large object over a slow Box link


class RcloneError(Exception):
    """A non-2xx response, or a transport failure, from the daemon."""

    def __init__(self, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class MinioSource:
    """rclone connection string for the backing MinIO bucket.

    Passing MinIO's credentials inline keeps them out of the rclone config
    file that gets mounted into the container — the config holds only the
    Box remote, whose token is the thing that actually needs to persist.
    """

    endpoint: str
    access_key: str
    secret_key: str
    region: str = "us-east-1"

    def fs(self) -> str:
        parts = [
            ":s3",
            "provider=Minio",
            f"endpoint={_escape(self.endpoint)}",
            f"access_key_id={_escape(self.access_key)}",
            f"secret_access_key={_escape(self.secret_key)}",
            f"region={_escape(self.region)}",
            "force_path_style=true",
        ]
        return ",".join(parts) + ":"


def _escape(value: str) -> str:
    """Quote a connection-string value if it holds a separator character."""
    if any(ch in value for ch in ',"'):
        return '"' + value.replace('"', '""') + '"'
    return value


class RcloneRC:
    """Minimal JSON-over-HTTP client for the rclone remote-control API."""

    def __init__(
        self,
        base_url: str,
        user: str = "",
        password: str = "",
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._auth = ""
        if user:
            token = b64encode(f"{user}:{password}".encode()).decode()
            self._auth = f"Basic {token}"

    def call(self, method: str, payload: dict) -> dict:
        body = json.dumps(payload).encode()
        request = urllib.request.Request(
            f"{self.base_url}/{method}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        if self._auth:
            request.add_header("Authorization", self._auth)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            detail = _error_detail(exc)
            raise RcloneError(
                f"{method} failed ({exc.code}): {detail}",
                retryable=_is_retryable(exc.code, detail),
            ) from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RcloneError(
                f"{method} transport error: {redact(str(exc))}", retryable=True
            ) from exc

    def noop(self) -> dict:
        return self.call("rc/noop", {})

    def version(self) -> str:
        return str(self.call("core/version", {}).get("version", "unknown"))

    def copy_file(
        self, src_fs: str, src_remote: str, dst_fs: str, dst_remote: str
    ) -> None:
        """Copy one object, renaming it — the whole job in one call."""
        self.call(
            "operations/copyfile",
            {
                "srcFs": src_fs,
                "srcRemote": src_remote,
                "dstFs": dst_fs,
                "dstRemote": dst_remote,
            },
        )

    def stat(self, fs: str, remote: str) -> dict | None:
        """Metadata for one destination path, or None when it is absent."""
        result = self.call("operations/stat", {"fs": fs, "remote": remote})
        return result.get("item") or None

    def stats(self) -> dict:
        return self.call("core/stats", {})


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read() or b"{}")
    except (json.JSONDecodeError, OSError):
        reason = exc.reason if isinstance(exc.reason, str) else str(exc.reason)
        return redact(reason)
    return redact(str(payload.get("error") or payload))


# rclone echoes the failing remote back in its errors, and ours is a
# connection string carrying MinIO's root credentials. Scrub them before the
# message reaches a log line.
SECRET_PARAMS = ("secret_access_key", "access_key_id", "rc-pass")
_SECRET_RE = re.compile(
    r"(" + "|".join(SECRET_PARAMS) + r")=([^,\s:\"]+)", re.IGNORECASE
)


def redact(text: str) -> str:
    return _SECRET_RE.sub(r"\1=***", text)


# Box answers a call it throttled with 429, and its upload hosts return 5xx
# under load. Both clear on their own, so the worker retries rather than
# burning the object for the whole run.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
RETRYABLE_MARKERS = (
    "rate_limit",
    "too many requests",
    "connection reset",
    "timeout",
    "eof",
    "temporarily",
)


def _is_retryable(status: int, detail: str) -> bool:
    if status in RETRYABLE_STATUS:
        return True
    lowered = detail.lower()
    return any(marker in lowered for marker in RETRYABLE_MARKERS)
