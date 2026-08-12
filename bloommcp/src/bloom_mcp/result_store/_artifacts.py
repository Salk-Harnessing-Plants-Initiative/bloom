"""Shared content-addressing for committed artifacts.

Both the Supabase adapter and the fake compute per-artifact hashes, logical
keys, and byte sizes the same way, so a single parity test covers both. The
SHA-256 is computed over the exact staged bytes — the same bytes the adapter
uploads — never an S3/MinIO ETag. Also shared: rebuilding that same
``OutputLink`` shape later, at read time, for an already-committed run
(bloom#599's ``get_download_links``).
"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from pathlib import Path
from typing import Callable, Optional

from bloom_mcp.contract.models import OutputLink

from .ports import CorruptRunLinksError

# Signed-URL expiry (bloom#581) — a fixed constant, not a per-call parameter or
# env var: the issue's own framing is "a real short-lived signed URL," and an
# hour comfortably outlasts a single chat session. Shared by both ResultStore
# adapters (SupabaseResultStore's real signing call, FakeResultStore's
# synthesized URL's query string) so the two never drift independently.
SIGNED_URL_EXPIRES_SECONDS = 3600


class KeyScopeGuardError(RuntimeError):
    """Raised by ``build_output_links``'s #598 key-scoping guard.

    A subclass of ``RuntimeError`` (not a new ``ResultStoreError``, per
    design.md's decision), so every existing ``except RuntimeError`` /
    ``except Exception`` still catches it unchanged. The dedicated type
    exists only so ``commit()``'s except block can tell this structural,
    never-transient failure apart from an actual transient one (a real
    network blip, a genuine writer race) and word the resulting
    ``CommitFailedError`` message accordingly — see
    ``supabase_store.py``/``fake_store.py``'s ``except`` blocks.
    """


def validate_outputs(outputs: dict[str, str]) -> None:
    """Reject an empty output set or a relative path that escapes the run dir.

    Enforces the Tier-1 "no artifact without its hash" invariant (a run must
    write at least one artifact) and guards the storage key against traversal
    even though today's callers pass hardcoded literal names.
    """
    if not outputs:
        raise ValueError("commit requires at least one output; got none")
    for rel in outputs.values():
        pure = PurePosixPath(rel)
        if pure.is_absolute() or ".." in pure.parts:
            raise ValueError(f"output path must stay within the run dir; got {rel!r}")


def hash_outputs(
    staging_dir: Path,
    outputs: dict[str, str],
    key_for: Callable[[str], str],
) -> tuple[dict[str, str], dict[str, str], dict[str, int]]:
    """Return ``(output_keys, output_sha256, output_size_bytes)``, each keyed
    identically to ``outputs``.

    ``outputs`` maps a logical name to a path relative to ``staging_dir``;
    ``key_for(rel)`` returns the logical storage key for that relative path.
    ``output_size_bytes`` is the exact staged byte count (bloom#581) — free,
    since the bytes are already read into memory to hash.
    """
    output_keys: dict[str, str] = {}
    output_sha256: dict[str, str] = {}
    output_size_bytes: dict[str, int] = {}
    for name, rel in outputs.items():
        data = (Path(staging_dir) / rel).read_bytes()
        output_sha256[name] = hashlib.sha256(data).hexdigest()
        output_keys[name] = key_for(rel)
        output_size_bytes[name] = len(data)
    return output_keys, output_sha256, output_size_bytes


def build_output_links(
    output_keys: dict[str, str],
    output_sha256: dict[str, str],
    output_size_bytes: dict[str, int],
    *,
    expected_prefix: str,
    url_for: Optional[Callable[[str], str]] = None,
    path_for: Optional[Callable[[str], str]] = None,
) -> dict[str, OutputLink]:
    """Build the per-output ``OutputLink`` dict ``commit()`` attaches to its
    ``StoredRun`` (bloom#581, #642 follow-up). Exactly one of ``url_for``/
    ``path_for`` must be given: ``url_for(key)`` supplies a signed/served URL
    (a real signed call for ``SupabaseResultStore``, a synthesized string for
    ``FakeResultStore``); ``path_for(key)`` supplies the resolved absolute
    filesystem path instead, for the local backend, which has no URL to sign
    or serve — the caller already has direct filesystem access to a file
    bloommcp just wrote.

    ``expected_prefix`` is the prefix ``commit()`` itself just computed for
    this run (bloom#598) — every key in ``output_keys`` MUST fall under it,
    since a key outside it would mean signing/pathing something this run did
    not itself just upload. Checked before ``url_for``/``path_for`` is ever
    called, so a violation never reaches either primitive (neither performs
    an ownership check of its own). A mismatch is a structural bug, never a
    caller-input condition — raises :class:`KeyScopeGuardError`, which both
    adapters' ``commit()`` already catch via their existing broad ``except
    Exception`` and convert to ``CommitFailedError``, the same fail-closed/
    cleanup path a signing failure already takes.

    ``expected_prefix`` itself must be non-empty: ``str.startswith("")`` is
    always ``True``, so an empty/falsy prefix would silently accept every key
    and defeat the guard entirely rather than raising — checked explicitly so
    that misconfiguration fails loudly instead of quietly no-op'ing.
    """
    if (url_for is None) == (path_for is None):
        raise ValueError("exactly one of url_for or path_for must be given")
    if not expected_prefix:
        raise KeyScopeGuardError(
            f"expected_prefix must be non-empty; got {expected_prefix!r}"
        )
    for name, key in output_keys.items():
        if not key.startswith(expected_prefix):
            raise KeyScopeGuardError(
                f"output key {key!r} (output {name!r}) is outside the "
                f"expected run prefix {expected_prefix!r}"
            )
    links = {}
    for name in output_keys:
        key = output_keys[name]
        url = url_for(key) if url_for else None
        path = path_for(key) if path_for else None
        # url is now Optional on OutputLink (the local backend legitimately
        # has none) — Pydantic's type check alone no longer rejects a None
        # here, so a signing call that yields nothing usable must fail loudly
        # rather than silently commit a link with no URL and no path either.
        if url_for and not url:
            raise ValueError(f"url_for returned no usable URL for key {key!r}")
        links[name] = OutputLink(
            key=key,
            url=url,
            path=path,
            sha256=output_sha256[name],
            size_bytes=output_size_bytes[name],
        )
        for name in output_keys
    }


def build_download_links(
    output_keys: dict[str, str],
    output_sha256: dict[str, str],
    url_for: Callable[[str], str],
    size_for: Callable[[str], int],
    *,
    expected_prefix: str,
) -> dict[str, OutputLink]:
    """Build the per-output ``OutputLink`` dict ``get_download_links`` attaches
    when re-signing an already-committed run (bloom#599).

    ``url_for(key)``/``size_for(key)`` supply the URL/size — a real signed
    call and a live ``StorageBackend.get_object_size`` call for
    ``SupabaseResultStore``, an already-recorded value for ``FakeResultStore``
    (which never uploads real bytes for a live lookup to meaningfully
    target) — so this one assembly step is shared by both adapters.

    ``expected_prefix`` is recomputed *fresh at read time* from
    ``(experiment, tool_class, the resolved run's version_dir)`` — this is
    deliberately **not** the same ``expected_prefix`` parameter
    ``build_output_links`` takes (that one is the *write*-path guard on
    ``commit()``, from ``add-bloommcp-signed-url-key-scoping``); this
    function's caller derives its own prefix independently, so this read
    path carries no ordering dependency on that change's merge status. A key
    outside it is never a caller-input condition (every ``output_keys`` value
    here came from a manifest this same lookup already resolved) — it
    signals corrupt manifest data or a resolution bug, and raises
    :class:`CorruptRunLinksError` before either ``url_for`` or ``size_for`` is
    called for that key, so neither the signing nor the sizing primitive
    (both of which perform no ownership check of their own) is ever reached
    for an out-of-scope key.
    """
    if not expected_prefix:
        raise CorruptRunLinksError(
            f"expected_prefix must be non-empty; got {expected_prefix!r}"
        )
    for name, key in output_keys.items():
        if not key.startswith(expected_prefix):
            raise CorruptRunLinksError(
                f"output key {key!r} (output {name!r}) is outside the "
                f"expected run prefix {expected_prefix!r}"
            )
    return {
        name: OutputLink(
            key=key,
            url=url_for(key),
            sha256=output_sha256[name],
            size_bytes=size_for(key),
        )
        for name, key in output_keys.items()
    }
