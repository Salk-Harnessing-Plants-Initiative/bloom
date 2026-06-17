"""Canonical provenance for the bloom-mcp tool contract.

`Provenance` is stamped at most once per tool call at **contract time** (around
delegation, before any artifact is written; skipped when input validation fails,
and discarded on any later failure path) and is the single source of truth for
the manifest `VersionEntry` — there is no parallel provenance record. The
per-artifact content hashes (`output_sha256`) and logical storage keys
(`output_keys`) are **not** contract-time fields: the artifact bytes do not
exist until the tool writes them to staging, so they are populated at commit by
the `ResultStore` (Tier 2) and merged into the same single version entry.

The exact-environment pointer (`resolve_environment`) records *which environment
produced the run* — the container image digest, or the `bloom-mcp` version whose
committed `uv.lock` reproduces the env. `code_versions` is a human-readable
trace; only the locked environment pins the library math (numpy/scipy/sklearn).
"""

from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from bloom_mcp.storage.code_versions import get_code_versions
from bloom_mcp.storage.schema import CodeVersions, VersionEntry

# Set at container build to the image digest (`sha256:…`); the tightest
# exact-environment identifier when bloom-mcp runs containerized.
_IMAGE_DIGEST_ENV = "BLOOM_MCP_IMAGE_DIGEST"

# Inclusive-exclusive upper bound for a seed: numpy/sklearn accept [0, 2**32).
SEED_MAX = 2**32


def resolve_seed(seed: Optional[int]) -> int:
    """Resolve the seed actually used: the given value, or a fresh integer.

    Recording the *resolved* seed (never null) is what makes a stochastic run
    reproducible — replaying the recorded integer reproduces the artifact. A
    provided seed must be a plain ``int`` in ``[0, SEED_MAX)`` (``bool`` is
    rejected, since ``True``/``False`` would silently coerce to ``1``/``0``).
    """
    if seed is None:
        return secrets.randbelow(SEED_MAX)
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError(f"seed must be an int in [0, {SEED_MAX}); got {seed!r}")
    if not 0 <= seed < SEED_MAX:
        raise ValueError(f"seed must be in [0, {SEED_MAX}); got {seed}")
    return seed


def _uv_lock_hash() -> Optional[str]:
    """Return a short content hash of the nearest committed ``uv.lock``, if any."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        lock = parent / "uv.lock"
        if lock.is_file():
            try:
                digest = hashlib.sha256(lock.read_bytes()).hexdigest()
            except OSError:
                return None
            return f"uvlock:{digest[:16]}"
    return None


def resolve_environment() -> Optional[str]:
    """Resolve the exact-environment pointer by precedence.

    Precedence: container image digest (`BLOOM_MCP_IMAGE_DIGEST`) → `bloom-mcp`
    version → `uv.lock` content hash. Returns None only when none resolves
    (optional for schema back-compat); a persisted run (Tier 2) is expected to
    carry a value that actually pins a reproducible environment. A digest is
    accepted only when it is a non-empty `sha256:…` string (whitespace stripped);
    a malformed digest falls through to the next source rather than being stored.
    """
    digest = os.getenv(_IMAGE_DIGEST_ENV)
    if digest:
        digest = digest.strip()
        if digest.startswith("sha256:") and len(digest) > len("sha256:"):
            return digest

    versions = get_code_versions()
    if versions.bloommcp:
        lock = _uv_lock_hash()
        suffix = f"+{lock}" if lock else ""
        return f"bloommcp=={versions.bloommcp}{suffix}"

    return _uv_lock_hash()


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 'Z' string (seconds)."""
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )


class Provenance(BaseModel):
    """The canonical contract-time provenance for a single tool call."""

    tool: str
    params: dict
    # None only for a non-stochastic tool (one whose delegate takes no
    # random_state); a stochastic call always records the resolved integer.
    seed: Optional[int] = None
    agent: str = "bloom_agent"
    input_sha256: Optional[str] = None
    code_versions: CodeVersions
    environment: Optional[str] = None
    created_at: str = Field(default_factory=_utc_now_iso)
    based_on_version: str = "raw"
    # Per-artifact fields — empty at contract time, filled at commit (Tier 2).
    outputs: dict[str, str] = Field(default_factory=dict)
    output_sha256: dict[str, str] = Field(default_factory=dict)
    output_keys: dict[str, str] = Field(default_factory=dict)
    user_label: Optional[str] = None
    version_dir: str = ""

    @classmethod
    def stamp(
        cls,
        *,
        tool: str,
        params: dict,
        seed: Optional[int] = None,
        agent: str = "bloom_agent",
        input_sha256: Optional[str] = None,
    ) -> "Provenance":
        """Stamp a contract-time record, resolving code_versions + environment once.

        `seed` is the *resolved* seed when the tool applied one, or None for a
        non-stochastic tool.
        """
        return cls(
            tool=tool,
            params=params,
            seed=seed,
            agent=agent,
            input_sha256=input_sha256,
            code_versions=get_code_versions(),
            environment=resolve_environment(),
        )

    def to_version_entry(self, *, version_id: str) -> VersionEntry:
        """Project this provenance into a manifest VersionEntry (schema v3).

        The version id is allocated at commit time by the `ResultStore`, so it is
        passed in. Per-artifact `output_sha256` / `output_keys` carry whatever is
        on the record (empty at contract time; filled by the store at commit).
        `input_sha256` is intentionally not set here — it lives on the manifest's
        `ExperimentBlock`, not the version entry.
        """
        return VersionEntry(
            id=version_id,
            created_at=self.created_at,
            tool=self.tool,
            params=self.params,
            based_on_version=self.based_on_version,
            code_versions=self.code_versions,
            outputs=self.outputs,
            user_label=self.user_label,
            version_dir=self.version_dir,
            seed=self.seed,
            agent=self.agent,
            environment=self.environment,
            output_sha256=self.output_sha256,
            output_keys=self.output_keys,
        )
