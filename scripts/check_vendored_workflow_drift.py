"""CI drift-check for `services/workflows/vendored/sleap-roots-pipeline.yaml`.

Fetches `sleap-roots-pipeline`'s canonical `sleap-roots-pipeline.yaml` at the
commit pinned in the sibling `SLEAP_ROOTS_PIPELINE_REF` file and diffs it
byte-for-byte against the vendored copy. This is the one network fetch in the
whole vendoring mechanism (bloom #737) — it runs only here, in CI, never in
the running `workflows` service or its container build.

Distinguishes, in its exit message, a failed upstream fetch (transient — the
check could not run, re-run the job) from a genuine content mismatch (real
drift — the vendored copy or the pin needs a human decision). These are
different problems requiring different responses and must not look the same.
"""

from __future__ import annotations

import http.client
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
VENDORED_DIR = REPO_ROOT / "services" / "workflows" / "vendored"
VENDORED_FILE = VENDORED_DIR / "sleap-roots-pipeline.yaml"
REF_FILE = VENDORED_DIR / "SLEAP_ROOTS_PIPELINE_REF"

RAW_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/talmolab/sleap-roots-pipeline/"
    "{sha}/sleap-roots-pipeline.yaml"
)
REQUEST_TIMEOUT_SECONDS = 15.0
RETRY_DELAY_SECONDS = 3.0
_SHA_RE = re.compile(r"[0-9a-f]{40}")


class FetchError(Exception):
    """The upstream fetch itself failed — transient, not a content mismatch."""


class PinNotFoundError(FetchError):
    """The pinned commit's raw content returned HTTP 404 — the pin itself no
    longer resolves upstream (e.g. its branch was deleted and the commit was
    garbage-collected), not a transient network problem. Retrying can never
    fix this; a human must re-pin SLEAP_ROOTS_PIPELINE_REF."""


def fetch_canonical_file(url: str, timeout: float = REQUEST_TIMEOUT_SECONDS) -> bytes:
    """One fetch attempt. Raises FetchError/PinNotFoundError (never a raw
    urllib/http.client exception) on any failure, so callers never need to
    know urllib's exception hierarchy — notably, `urllib.error.HTTPError` is
    itself a subclass of `URLError`, so it must be special-cased *before* the
    broader `except` below, or a 404 (permanent — re-pin required) would be
    silently treated the same as a transient network blip."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise PinNotFoundError(str(exc)) from exc
        raise FetchError(str(exc)) from exc
    except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
        raise FetchError(str(exc)) from exc


def fetch_with_retry(
    url: str,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
    retries: int = 1,
    delay_seconds: float = RETRY_DELAY_SECONDS,
) -> bytes:
    """Fetch with one bounded retry (a fixed short delay, not exponential
    backoff) before giving up — a single transient failure shouldn't read the
    same as a real drift on a check that's proposed as blocking. Never
    retries a PinNotFoundError: a 404 means the pin itself is invalid, and no
    amount of retrying the same URL changes that."""
    last_exc: FetchError | None = None
    for attempt in range(retries + 1):
        try:
            return fetch_canonical_file(url, timeout=timeout)
        except PinNotFoundError:
            raise
        except FetchError as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(delay_seconds)
    raise FetchError(
        f"upstream fetch failed after {retries + 1} attempt(s): {last_exc}"
    )


def check_drift(vendored_path: Path = VENDORED_FILE, ref_path: Path = REF_FILE) -> int:
    """Returns a process exit code: 0 (match), 1 (content drift), 2 (the
    upstream fetch itself failed, transiently or because the pin no longer
    resolves), or 3 (the pin file itself is missing or malformed — never
    reaches the network)."""
    try:
        sha = ref_path.read_text().strip()
    except OSError as exc:
        print(f"PIN FILE MISSING OR UNREADABLE: {ref_path} ({exc})", file=sys.stderr)
        return 3

    if not _SHA_RE.fullmatch(sha):
        print(
            f"PIN FILE MALFORMED: {ref_path} does not contain a 40-character "
            f"commit SHA (got {sha!r})",
            file=sys.stderr,
        )
        return 3

    url = RAW_URL_TEMPLATE.format(sha=sha)

    try:
        upstream_content = fetch_with_retry(url)
    except PinNotFoundError as exc:
        print(
            f"PIN NO LONGER RESOLVES UPSTREAM (re-pin required, not transient — "
            f"retrying will not help): {exc}",
            file=sys.stderr,
        )
        return 2
    except FetchError as exc:
        print(
            f"UPSTREAM FETCH FAILED (transient — re-run this job): {exc}",
            file=sys.stderr,
        )
        return 2

    vendored_content = vendored_path.read_bytes()
    if upstream_content != vendored_content:
        print(
            f"DRIFT DETECTED: {vendored_path} no longer matches "
            f"sleap-roots-pipeline's sleap-roots-pipeline.yaml at pinned commit "
            f"{sha}. Either the vendored copy was hand-edited without bumping "
            "the pin, or the pin was bumped without updating the vendored copy "
            "to match.",
            file=sys.stderr,
        )
        return 1

    print(f"OK: vendored copy matches sleap-roots-pipeline at {sha}")
    return 0


if __name__ == "__main__":
    sys.exit(check_drift())
