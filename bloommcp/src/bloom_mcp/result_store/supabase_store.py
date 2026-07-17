"""Supabase-backed :class:`ResultStore` — wraps the deployed storage layer.

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
from bloom_mcp.storage import (
    AnalysisDir,
    ExperimentBlock,
    Manifest,
    next_version_id,
    version_dir_name,
    write_manifest,
)

from ._artifacts import hash_outputs, validate_outputs
from .ports import (
    CommitFailedError,
    RunHandle,
    RunNotFoundError,
    RunStateError,
    StoredRun,
)

if TYPE_CHECKING:
    from bloom_mcp.contract.provenance import Provenance

logger = logging.getLogger(__name__)

_OUTPUT_PREFIX = "bloommcp_output"

# Bounded attempts to allocate a free version_id before giving up. Applies at
# the pre-upload collision check (see `commit`); exhausting it there is a
# cheap failure since nothing has been uploaded yet.
_MAX_ID_ATTEMPTS = 3


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
    ) -> RunHandle:
        adir = AnalysisDir(self._output_root, experiment, tool_class)
        # This allocation is provisional: `commit` re-reads the manifest fresh
        # and reallocates on collision before uploading anything, so two
        # `create_run` calls racing to the same id here is not a correctness
        # issue — only `commit`'s ordering is. See `commit`'s pre-upload check.
        version_id = next_version_id(adir.read_manifest())
        version_dir = version_dir_name(version_id, user_label)
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

        uploaded_keys: list[str] = []
        try:
            # Finalize version_id + version_dir together, before any upload:
            # both are derived from version_id, so choosing them separately
            # (e.g. relabeling the id after uploading) would leave a committed
            # entry pointing at a version_dir/keys that disagree with its own
            # id. Re-read fresh here rather than trust the manifest `create_run`
            # saw — another writer may have committed since.
            version_id = state.version_id
            version_dir = state.version_dir
            existing = adir.read_manifest()
            attempts = 0
            while existing is not None and any(
                v.id == version_id for v in existing.versions
            ):
                attempts += 1
                if attempts >= _MAX_ID_ATTEMPTS:
                    raise RuntimeError(
                        f"could not allocate a free version id after {attempts} "
                        f"attempts (last tried: {version_id!r})"
                    )
                version_id = next_version_id(existing)
                version_dir = version_dir_name(version_id, state.user_label)

            def key_for(rel: str) -> str:
                return adir.key(f"{version_dir}/{rel}")

            output_keys, output_sha256 = hash_outputs(run.staging_dir, outputs, key_for)
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

            # Re-read once more immediately before writing: guards against a
            # writer that claimed this exact version_id while this commit's
            # upload was in flight. On collision this is treated like any
            # other commit failure (cleanup + retry-by-caller) rather than
            # overwriting or relabeling the entry that got there first.
            fresh = adir.read_manifest()
            if fresh is not None and any(v.id == version_id for v in fresh.versions):
                raise RuntimeError(
                    f"version {version_id!r} was claimed by another writer "
                    f"during upload"
                )

            if fresh is None:
                manifest = Manifest(
                    experiment=ExperimentBlock(
                        filename=adir.experiment_filename,
                        source_path=str(state.source_csv) if state.source_csv else "",
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

            # Manifest is written last: an upload failure above leaves `latest`
            # un-advanced rather than pointing at a half-written version.
            write_manifest(adir.path, manifest)
        except Exception as exc:
            self._cleanup_uploaded(uploaded_keys, adir)
            # Leave the handle open and the staging dir intact so the caller can
            # retry — a retry re-enters commit() and re-allocates a fresh id
            # against a then-current manifest. The detail is logged server-side
            # only; the agent-facing message carries no Supabase URL / object
            # path.
            logger.exception(
                "ResultStore.commit failed for %s/%s", adir.tool_class, adir.stem
            )
            raise CommitFailedError(
                f"commit failed for {adir.tool_class}/{adir.stem} (transient — retry)"
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
            _sc.delete_files(keys)
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
