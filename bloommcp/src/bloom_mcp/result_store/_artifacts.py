"""Shared content-addressing for committed artifacts.

Both the Supabase adapter and the fake compute per-artifact hashes, logical
keys, and byte sizes the same way, so a single parity test covers both. The
SHA-256 is computed over the exact staged bytes — the same bytes the adapter
uploads — never an S3/MinIO ETag.
"""

from __future__ import annotations

import hashlib
from pathlib import PurePosixPath
from pathlib import Path
from typing import Callable

from bloom_mcp.contract.models import OutputLink

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
    url_for: Callable[[str], str],
    *,
    expected_prefix: str,
) -> dict[str, OutputLink]:
    """Build the per-output ``OutputLink`` dict ``commit()`` attaches to its
    ``StoredRun`` (bloom#581). ``url_for(key)`` supplies the URL — a real
    signed call for ``SupabaseResultStore``, a synthesized string for
    ``FakeResultStore`` — so this one assembly step is shared by both.

    ``expected_prefix`` is the prefix ``commit()`` itself just computed for
    this run (bloom#598) — every key in ``output_keys`` MUST fall under it,
    since a key outside it would mean signing something this run did not
    itself just upload. Checked before any ``url_for`` call, so a violation
    never reaches the signing primitive (which performs no ownership check of
    its own). A mismatch is a structural bug, never a caller-input condition —
    raises :class:`KeyScopeGuardError`, which both adapters' ``commit()``
    already catch via their existing broad ``except Exception`` and convert
    to ``CommitFailedError``, the same fail-closed/cleanup path a signing
    failure already takes.

    ``expected_prefix`` itself must be non-empty: ``str.startswith("")`` is
    always ``True``, so an empty/falsy prefix would silently accept every key
    and defeat the guard entirely rather than raising — checked explicitly so
    that misconfiguration fails loudly instead of quietly no-op'ing.
    """
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
    return {
        name: OutputLink(
            key=output_keys[name],
            url=url_for(output_keys[name]),
            sha256=output_sha256[name],
            size_bytes=output_size_bytes[name],
        )
        for name in output_keys
    }
