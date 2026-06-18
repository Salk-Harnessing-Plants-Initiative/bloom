"""Shared content-addressing for committed artifacts.

Both the Supabase adapter and the fake compute per-artifact hashes and logical
keys the same way, so a single parity test covers both. The SHA-256 is computed
over the exact staged bytes — the same bytes the adapter uploads — never an
S3/MinIO ETag.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable


def hash_outputs(
    staging_dir: Path,
    outputs: dict[str, str],
    key_for: Callable[[str], str],
) -> tuple[dict[str, str], dict[str, str]]:
    """Return ``(output_keys, output_sha256)`` keyed identically to ``outputs``.

    ``outputs`` maps a logical name to a path relative to ``staging_dir``;
    ``key_for(rel)`` returns the logical storage key for that relative path.
    """
    output_keys: dict[str, str] = {}
    output_sha256: dict[str, str] = {}
    for name, rel in outputs.items():
        data = (Path(staging_dir) / rel).read_bytes()
        output_sha256[name] = hashlib.sha256(data).hexdigest()
        output_keys[name] = key_for(rel)
    return output_keys, output_sha256
