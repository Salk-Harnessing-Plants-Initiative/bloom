"""Backend-agnostic result-persistence port and its value types.

Tools depend on :class:`ResultStore`, never on ``AnalysisDir`` or
``supabase`` directly. Backend-specific concepts — the
``<tool_class>_<stem>`` directory scheme, ``v<N>`` ids, the ``latest`` pointer,
object keys — live inside the adapter; the port speaks in an **opaque**
``run_ref`` so a future orchestrator-owned / per-user-identity writer can
satisfy it without those concepts leaking to callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:  # avoid an import cycle; only used for typing
    from bloom_mcp.contract.models import OutputLink
    from bloom_mcp.contract.provenance import Provenance
    from bloom_mcp.data_access import SourceInfo
    from bloom_mcp.manifest.schema import VersionEntry


class ResultStoreError(Exception):
    """Base for write-port failures, with a caller-safe message."""


class RunNotFoundError(ResultStoreError):
    """No run matches the requested ``(experiment, tool_class, run_ref)``."""


class CorruptRunLinksError(ResultStoreError):
    """A resolved run's persisted ``output_keys`` fall outside its own expected
    object-key prefix (bloom#599's read-path guard).

    Raised by :meth:`ResultStore.get_download_links` before signing or sizing
    any output whose key does not fall under the freshly recomputed
    ``(experiment, tool_class, version_dir)`` prefix — never a caller-input
    condition, always a corrupt-manifest-data or resolution-bug signal.
    Structurally distinct from ``_artifacts.KeyScopeGuardError``
    (``add-bloommcp-signed-url-key-scoping``'s *write*-path guard on
    ``commit()``, a ``RuntimeError`` subclass): that one guards a key before
    it is ever uploaded/signed; this one guards a key already read back from
    a committed manifest. The two are independent by design (see that
    change's design.md) and living in different modules avoids any merge
    collision regardless of which change lands second.
    """


class RunStateError(ResultStoreError):
    """A run handle was misused — committed twice, or never created here."""


class CommitFailedError(ResultStoreError):
    """An upload or manifest write failed mid-commit (no partial run recorded)."""


class ManifestReadError(ResultStoreError):
    """Reading the version manifest failed.

    Covers everything :class:`ManifestIncompatibleError` doesn't: a transient
    storage/network blip, but also a truncated/corrupt ``manifest.json`` or a
    permanent permission denial — this type alone does not imply the failure
    is safe to retry.
    """


class ManifestIncompatibleError(ManifestReadError):
    """The version manifest's schema is unsupported by this server (missing
    or newer than the schema version this code understands).

    A subclass of :class:`ManifestReadError`, not a sibling — every existing
    ``except ManifestReadError`` / ``except ResultStoreError`` / ``except Exception``
    still catches it, but a caller that needs to distinguish "storage flaked" from
    "manifest schema unsupported" (e.g. before deciding whether a retry could ever
    help) can ``isinstance()``-check for this narrower type — check for it
    *before* a broader ``except ManifestReadError`` if branching on both, since
    this subclass would otherwise be swallowed by the parent's clause first.
    """


@dataclass
class RunHandle:
    """An in-progress run: write outputs into ``staging_dir``, then ``commit``.

    ``version_id`` is exposed before commit so tools that name files by version
    (e.g. dimred/clustering plots) can read it. ``_backend`` is adapter-private.
    """

    version_id: str
    staging_dir: Path
    manifest_path: str
    _backend: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class StoredRun:
    """A committed run, described without backend-specific types."""

    run_ref: str
    tool: str
    tool_class: str
    experiment: str
    version_dir: str
    manifest_path: str
    created_at: str
    outputs: dict[str, str]
    output_keys: dict[str, str]
    output_sha256: dict[str, str]
    seed: Optional[int]
    agent: Optional[str]
    environment: Optional[str]
    code_versions: dict[str, Optional[str]]
    input_validation: Optional[dict] = None
    source_id: Optional[int] = None
    source_name: Optional[str] = None
    # Per-output signed/served download links (bloom#581). Populated only by
    # `commit` — `get_run`/`list_runs` (and `from_version_entry`, which both of
    # those use) leave this `{}`, so listing/resolving a historical run never
    # eagerly signs a URL for it. Never persisted into the manifest.
    output_links: dict[str, "OutputLink"] = field(default_factory=dict)
    # A signed/served download link for this run's own `manifest.json`
    # (bloom#600). Populated only by `get_download_links` — never by
    # `create_run`/`commit`/`get_run`/`list_runs`/`from_version_entry`, which
    # all leave this `None`. Unlike `output_links`, this is never gated on
    # `output_keys` being non-empty: a run's manifest always exists once
    # committed, regardless of whether per-artifact output keys were ever
    # recorded for it. Never persisted into the manifest.
    manifest_url: Optional[str] = None

    @classmethod
    def from_version_entry(
        cls,
        entry: "VersionEntry",
        *,
        tool_class: str,
        experiment: str,
        manifest_path: str,
    ) -> "StoredRun":
        """Project a manifest :class:`VersionEntry` into a backend-neutral run."""
        return cls(
            run_ref=entry.id,
            tool=entry.tool,
            tool_class=tool_class,
            experiment=experiment,
            version_dir=entry.version_dir,
            manifest_path=manifest_path,
            created_at=entry.created_at,
            outputs=dict(entry.outputs),
            output_keys=dict(entry.output_keys),
            output_sha256=dict(entry.output_sha256),
            seed=entry.seed,
            agent=entry.agent,
            environment=entry.environment,
            code_versions=entry.code_versions.model_dump(mode="json"),
            input_validation=entry.input_validation,
            source_id=entry.source_id,
            source_name=entry.source_name,
        )


@runtime_checkable
class ResultStore(Protocol):
    """Persists versioned analysis runs without exposing the backend."""

    def create_run(
        self,
        *,
        experiment: str,
        tool_class: str,
        provenance: "Provenance",
        user_label: Optional[str] = None,
        source_csv: Optional[Path] = None,
        source: Optional["SourceInfo"] = None,
    ) -> RunHandle:
        """Allocate a version + staging dir for a new run on ``experiment``.

        ``source``, when given, identifies which DB source/pipeline-run backed
        the experiment read that produced this run (see
        ``bloom_mcp.data_access.SourceSelectable``); it is merged into the
        stored provenance so it survives to the committed ``VersionEntry``
        without the caller building a modified ``Provenance`` itself.
        """
        ...

    def commit(self, run: RunHandle, outputs: dict[str, str]) -> StoredRun:
        """Upload the staged ``outputs`` and record the run; return its links.

        ``outputs`` maps a logical name to a path relative to the run's staging
        directory.
        """
        ...

    def list_runs(self, experiment: str, tool_class: str) -> list[StoredRun]:
        """Return every recorded run for ``(experiment, tool_class)``."""
        ...

    def get_run(
        self,
        experiment: str,
        tool_class: str,
        run_ref: str = "latest",
    ) -> StoredRun:
        """Resolve a run by reference; ``"latest"`` resolves the most recent."""
        ...

    def get_download_links(
        self,
        experiment: str,
        tool_class: str,
        run_ref: str = "latest",
    ) -> StoredRun:
        """Resolve a run exactly as ``get_run`` does, but with ``output_links``
        and ``manifest_url`` freshly (re-)populated (bloom#599/#600) — the
        deliberate, caller-opted-in exception to ``get_run``/``list_runs``
        always returning them empty/``None``.

        Every ``size_bytes`` is resolved live via
        :meth:`StorageBackend.get_object_size` on every call — nothing is
        ever persisted for it, and no manifest/``Provenance``/``VersionEntry``
        field is read, written, or created for this purpose. A run whose
        ``output_keys`` is empty (e.g. a legacy v2 manifest entry, recorded
        before per-artifact keys existed) returns ``output_links == {}``
        rather than raising. Raises :class:`CorruptRunLinksError` if any
        persisted ``output_key`` falls outside the freshly recomputed
        ``(experiment, tool_class, version_dir)`` prefix, before signing or
        sizing it. A single output's lookup failure fails the whole call —
        never a partially-populated ``output_links``.

        ``manifest_url`` (bloom#600) is a signed/served link for this run's
        own ``manifest_path``, resolved independently of the ``output_keys``
        -empty short-circuit above — the manifest always exists for a
        committed run, so this is never skipped. A failure to sign it fails
        the whole call, identically to a per-output failure.
        """
        ...
