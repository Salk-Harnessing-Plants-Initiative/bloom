"""Supabase-backed :class:`ResultStore` — wraps the deployed storage layer.

Reuses the deployed versioning/staging/manifest/upload primitives
(``AnalysisDir``, ``versioning``, ``manifest``, ``supabase_client``), but —
unlike ``AnalysisWriter.commit``, which hand-rolls a provenance-lossy entry —
builds the v3 ``VersionEntry`` from the canonical :class:`Provenance` and fills
each artifact's ``output_sha256`` (over the exact uploaded bytes) and logical
``output_keys`` at commit. Tolerates pre-existing v2 manifests on read.
"""

from __future__ import annotations

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

from ._artifacts import hash_outputs
from .ports import (
    CommitFailedError,
    ResultStoreError,
    RunHandle,
    RunNotFoundError,
    RunStateError,
    StoredRun,
)

if TYPE_CHECKING:
    from bloom_mcp.contract.provenance import Provenance

_OUTPUT_PREFIX = "bloommcp_output"


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
        adir = state.adir

        def key_for(rel: str) -> str:
            return adir.key(f"{state.version_dir}/{rel}")

        try:
            output_keys, output_sha256 = hash_outputs(run.staging_dir, outputs, key_for)
            # Upload the same staged bytes that were just hashed.
            for _name, rel in outputs.items():
                _sc.upload_file(key_for(rel), run.staging_dir / rel)

            prov = state.provenance.model_copy(
                update={
                    "outputs": dict(outputs),
                    "output_keys": output_keys,
                    "output_sha256": output_sha256,
                    "version_dir": state.version_dir,
                    "user_label": state.user_label,
                }
            )
            entry = prov.to_version_entry(version_id=state.version_id)

            sha = ""
            if state.source_csv is not None and Path(state.source_csv).exists():
                sha = adir.input_sha256(Path(state.source_csv))

            existing = adir.read_manifest()
            if existing is None:
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
                existing.versions.append(entry)
                existing.latest = entry.id
                if not existing.experiment.input_sha256 and sha:
                    existing.experiment.input_sha256 = sha
                manifest = existing

            # Manifest is written last: an upload failure above leaves `latest`
            # un-advanced rather than pointing at a half-written version.
            write_manifest(adir.path, manifest)
        except ResultStoreError:
            raise
        except Exception as exc:  # caller-safe; no traceback leak
            raise CommitFailedError(
                f"commit failed for {adir.tool_class}/{adir.stem}: {exc}"
            ) from exc
        finally:
            shutil.rmtree(run.staging_dir, ignore_errors=True)
            state.committed = True

        return StoredRun.from_version_entry(
            entry,
            tool_class=adir.tool_class,
            experiment=adir.experiment_filename,
            manifest_path=run.manifest_path,
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
