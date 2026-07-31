"""Supabase-backed :class:`ResultStore` — wraps the deployed manifest layer.

Reuses the deployed versioning/staging/manifest/upload primitives
(``AnalysisDir``, ``versioning``, ``manifest``, ``supabase_client``), but —
unlike ``AnalysisWriter.commit``, which hand-rolls a provenance-lossy entry —
builds the v3 ``VersionEntry`` from the canonical :class:`Provenance` and fills
each artifact's ``output_sha256`` (over the exact uploaded bytes) and logical
``output_keys`` at commit. Tolerates pre-existing v2 manifests on read.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from bloom_mcp import supabase_client as _sc
from bloom_mcp.manifest import (
    AnalysisDir,
    ExperimentBlock,
    Manifest,
    next_version_id,
    version_dir_name,
    write_manifest,
)

from ._artifacts import hash_outputs, validate_outputs
from ._locks import KeyedLock
from .ports import (
    CommitFailedError,
    RunHandle,
    RunNotFoundError,
    RunStateError,
    StoredRun,
)

if TYPE_CHECKING:
    from bloom_mcp.contract.provenance import Provenance
    from bloom_mcp.data_access import SourceInfo

logger = logging.getLogger(__name__)

_OUTPUT_PREFIX = "bloommcp_output"

# Bounded attempts to allocate a free version_id before giving up. Applies at
# the pre-upload collision check (see `commit`); exhausting it there is a
# cheap failure since nothing has been uploaded yet.
_MAX_ID_ATTEMPTS = 3

# Network timeout for the best-effort cleanup delete, shorter than the
# default (20s, storage3's DEFAULT_TIMEOUT) a real upload might reasonably
# wait on: cleanup is best-effort, so a hung delete should give up and let
# the original, actionable CommitFailedError surface promptly rather than
# hold the caller for as long as a load-bearing call would.
_CLEANUP_TIMEOUT_SECONDS = 5.0


# `commit` is dispatched by FastMCP's Starlette server via a thread pool, so
# two calls for the same (output_root, experiment, tool_class) can genuinely
# run concurrently *within this one process* — not just a hypothetical future
# multi-instance deployment. Without serializing them, both can pass the
# pre-upload collision check before either writes the manifest, compute the
# *same* deterministic version_dir (version_id + today's date, no nonce), and
# upload to the same object keys; whichever loses the manifest race then
# "cleans up" keys the winner's now-committed manifest entry depends on —
# deleting already-committed data. A per-key lock (`KeyedLock`, shared with
# `FakeResultStore` — see `_locks.py`) makes `commit` calls for the same
# manifest fully mutually exclusive, so the second call's pre-upload check
# always sees the first's fresh entry *before* uploading anything.
def _commit_lock(output_root: str, experiment: str, tool_class: str) -> KeyedLock:
    # "supabase" namespaces this adapter's keys apart from FakeResultStore's
    # in the shared registry — the two must never contend on each other's
    # locks even if given identical (output_root, experiment, tool_class).
    return KeyedLock(("supabase", output_root, experiment, tool_class))


@dataclass
class _SupabaseRunState:
    adir: AnalysisDir
    version_id: str
    version_dir: str
    provenance: "Provenance"
    user_label: Optional[str]
    source_csv: Optional[Path]
    committed: bool = False


class SupabaseResultStore:
    """Persists runs to Supabase Storage with full v3 provenance."""

    def __init__(self, output_root: str = _OUTPUT_PREFIX) -> None:
        self._output_root = output_root

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
        if source is not None:
            provenance = provenance.model_copy(
                update={
                    "source_id": source.source_id,
                    "source_name": source.source_name,
                }
            )
        adir = AnalysisDir(self._output_root, experiment, tool_class)
        # Single-writer assumption (see _WIKI/BLOOMMCP/storage-workflow.md):
        # version_id is allocated from the current manifest now and the manifest
        # is re-read at commit without a compare-and-set, so two interleaved runs
        # can allocate the same v<N>. Safe under bloommcp's one-container
        # topology; a compare-and-set (or re-allocate-at-commit) is on the
        # roadmap (#324).
        version_id = next_version_id(adir.read_manifest())
        version_dir = version_dir_name(version_id, user_label)
        # No orphan cleanup if commit() is never reached (crash, or the tool errors
        # before committing) — the deleted AnalysisWriter had a best-effort __del__
        # for this; RunHandle has no equivalent yet. Pre-existing gap, tracked by #464.
        staging = Path(tempfile.mkdtemp(prefix="bloommcp_v_"))
        return RunHandle(
            version_id=version_id,
            staging_dir=staging,
            manifest_path=f"{adir.path}manifest.json",
            _backend=_SupabaseRunState(
                adir=adir,
                version_id=version_id,
                version_dir=version_dir,
                provenance=provenance,
                user_label=user_label,
                source_csv=source_csv,
            ),
        )

    def commit(self, run: RunHandle, outputs: dict[str, str]) -> StoredRun:
        state: Optional[_SupabaseRunState] = run._backend
        if state is None or state.committed:
            raise RunStateError("commit() on an unknown or already-committed run")
        validate_outputs(outputs)
        adir = state.adir

        # Serializes every commit for this (output_root, experiment, tool_class)
        # within this process — see the lock's module-level docstring for why
        # this is load-bearing, not defensive belt-and-suspenders.
        lock = _commit_lock(
            self._output_root, adir.experiment_filename, adir.tool_class
        )
        with lock:
            uploaded_keys: list[str] = []
            try:
                # Finalize version_id + version_dir together, before any
                # upload: both are derived from version_id, so choosing them
                # separately (e.g. relabeling the id after uploading) would
                # leave a committed entry pointing at a version_dir/keys that
                # disagree with its own id. Re-read fresh on every attempt
                # (not once, cached) — a stale snapshot can never re-collide
                # with the id `next_version_id` just computed from it, which
                # would make the bound unreachable through real contention.
                version_id = state.version_id
                version_dir = state.version_dir
                attempts = 0
                while True:
                    existing = adir.read_manifest()
                    if existing is None or not any(
                        v.id == version_id for v in existing.versions
                    ):
                        break
                    attempts += 1
                    if attempts >= _MAX_ID_ATTEMPTS:
                        raise RuntimeError(
                            f"could not allocate a free version id after "
                            f"{attempts} attempts (last tried: {version_id!r})"
                        )
                    version_id = next_version_id(existing)
                    version_dir = version_dir_name(version_id, state.user_label)
                run.version_id = version_id

                def key_for(rel: str) -> str:
                    return adir.key(f"{version_dir}/{rel}")

                output_keys, output_sha256 = hash_outputs(
                    run.staging_dir, outputs, key_for
                )
                # Upload the same staged bytes that were just hashed.
                for _name, rel in outputs.items():
                    key = key_for(rel)
                    _sc.upload_file(key, run.staging_dir / rel)
                    uploaded_keys.append(key)

                prov = state.provenance.model_copy(
                    update={
                        "outputs": dict(outputs),
                        "output_keys": output_keys,
                        "output_sha256": output_sha256,
                        "version_dir": version_dir,
                        "user_label": state.user_label,
                    }
                )
                entry = prov.to_version_entry(version_id=version_id)

                sha = ""
                if state.source_csv is not None and Path(state.source_csv).exists():
                    sha = adir.input_sha256(Path(state.source_csv))

                # Re-read once more immediately before writing: guards against
                # a writer that claimed this exact version_id while this
                # commit's upload was in flight. Under the lock above, no
                # *other* commit() call can be mid-flight for this same key —
                # this remains as defense-in-depth against a caller that
                # bypasses the lock (e.g. a future direct manifest writer) and
                # against the still-open multi-instance case documented below.
                fresh = adir.read_manifest()
                if fresh is not None and any(
                    v.id == version_id for v in fresh.versions
                ):
                    raise RuntimeError(
                        f"version {version_id!r} was claimed by another writer "
                        f"during upload"
                    )

                if fresh is None:
                    manifest = Manifest(
                        experiment=ExperimentBlock(
                            filename=adir.experiment_filename,
                            source_path=(
                                str(state.source_csv) if state.source_csv else ""
                            ),
                            input_sha256=sha,
                        ),
                        versions=[entry],
                        latest=entry.id,
                    )
                else:
                    fresh.versions.append(entry)
                    fresh.latest = entry.id
                    if not fresh.experiment.input_sha256 and sha:
                        fresh.experiment.input_sha256 = sha
                    manifest = fresh

                # Manifest is written last: an upload failure above leaves
                # `latest` un-advanced rather than pointing at a half-written
                # version.
                write_manifest(adir.path, manifest)
            except Exception as exc:
                self._cleanup_uploaded(uploaded_keys, adir)
                # Leave the handle open and the staging dir intact so the
                # caller can retry — a retry re-enters commit() and
                # re-allocates a fresh id against a then-current manifest. The
                # detail is logged server-side only; the agent-facing message
                # carries no Supabase URL / object path.
                logger.exception(
                    "ResultStore.commit failed for %s/%s", adir.tool_class, adir.stem
                )
                raise CommitFailedError(
                    f"commit failed for {adir.tool_class}/{adir.stem} "
                    f"(transient — retry)"
                ) from exc

            # Success only: tear down staging and seal the handle.
            shutil.rmtree(run.staging_dir, ignore_errors=True)
            state.committed = True
            return StoredRun.from_version_entry(
                entry,
                tool_class=adir.tool_class,
                experiment=adir.experiment_filename,
                manifest_path=run.manifest_path,
            )

    @staticmethod
    def _cleanup_uploaded(keys: list[str], adir: AnalysisDir) -> None:
        """Best-effort delete objects a failed commit already uploaded.

        Never raises: a cleanup failure is logged server-side and must not
        replace or mask the commit error that triggered it.
        """
        if not keys:
            return
        try:
            _sc.delete_files(keys, timeout_seconds=_CLEANUP_TIMEOUT_SECONDS)
        except Exception:
            logger.warning(
                "ResultStore.commit cleanup failed for %s/%s — %d object(s) "
                "may be orphaned",
                adir.tool_class,
                adir.stem,
                len(keys),
                exc_info=True,
            )

    def list_runs(self, experiment: str, tool_class: str) -> list[StoredRun]:
        adir = AnalysisDir(self._output_root, experiment, tool_class)
        manifest_path = f"{adir.path}manifest.json"
        return [
            StoredRun.from_version_entry(
                entry,
                tool_class=tool_class,
                experiment=experiment,
                manifest_path=manifest_path,
            )
            for entry in adir.list_versions()
        ]

    def get_run(
        self,
        experiment: str,
        tool_class: str,
        run_ref: str = "latest",
    ) -> StoredRun:
        adir = AnalysisDir(self._output_root, experiment, tool_class)
        entry = adir.get_version(run_ref)
        if entry is None:
            raise RunNotFoundError(f"No run {run_ref!r} for {tool_class}/{adir.stem}.")
        return StoredRun.from_version_entry(
            entry,
            tool_class=tool_class,
            experiment=experiment,
            manifest_path=f"{adir.path}manifest.json",
        )
