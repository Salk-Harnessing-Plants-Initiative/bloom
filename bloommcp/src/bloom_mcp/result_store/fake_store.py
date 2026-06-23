"""In-memory :class:`ResultStore` for tests — no Supabase, no manifest I/O."""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from bloom_mcp.storage.versioning import version_dir_name

from ._artifacts import hash_outputs, validate_outputs
from .ports import (
    RunHandle,
    RunNotFoundError,
    RunStateError,
    StoredRun,
)

if TYPE_CHECKING:
    from bloom_mcp.contract.provenance import Provenance


@dataclass
class _FakeRunState:
    experiment: str
    tool_class: str
    version_id: str
    version_dir: str
    prefix: str
    provenance: "Provenance"
    user_label: Optional[str]


class FakeResultStore:
    """Records versioned runs in memory, mirroring :class:`SupabaseResultStore`."""

    def __init__(self, output_root: str = "bloommcp_output") -> None:
        self._output_root = output_root
        self._runs: dict[tuple[str, str], list[StoredRun]] = {}
        self._open: set[int] = set()

    def create_run(
        self,
        *,
        experiment: str,
        tool_class: str,
        provenance: "Provenance",
        user_label: Optional[str] = None,
        source_csv: Optional[Path] = None,
    ) -> RunHandle:
        existing = self._runs.get((experiment, tool_class), [])
        version_id = f"v{len(existing) + 1}"
        version_dir = version_dir_name(version_id, user_label)
        prefix = f"{self._output_root}/{tool_class}_{_stem(experiment)}/"
        staging = Path(tempfile.mkdtemp(prefix="fake_v_"))
        handle = RunHandle(
            version_id=version_id,
            staging_dir=staging,
            manifest_path=f"{prefix}manifest.json",
            _backend=_FakeRunState(
                experiment=experiment,
                tool_class=tool_class,
                version_id=version_id,
                version_dir=version_dir,
                prefix=prefix,
                provenance=provenance,
                user_label=user_label,
            ),
        )
        self._open.add(id(handle))
        return handle

    def commit(self, run: RunHandle, outputs: dict[str, str]) -> StoredRun:
        if id(run) not in self._open:
            raise RunStateError("commit() on an unknown or already-committed run")
        validate_outputs(outputs)
        self._open.discard(id(run))
        state: _FakeRunState = run._backend

        def key_for(rel: str) -> str:
            return f"{state.prefix}{state.version_dir}/{rel}"

        try:
            output_keys, output_sha256 = hash_outputs(run.staging_dir, outputs, key_for)
        finally:
            shutil.rmtree(run.staging_dir, ignore_errors=True)

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
        stored = StoredRun.from_version_entry(
            entry,
            tool_class=state.tool_class,
            experiment=state.experiment,
            manifest_path=run.manifest_path,
        )
        self._runs.setdefault((state.experiment, state.tool_class), []).append(stored)
        return stored

    def list_runs(self, experiment: str, tool_class: str) -> list[StoredRun]:
        return list(self._runs.get((experiment, tool_class), []))

    def get_run(
        self,
        experiment: str,
        tool_class: str,
        run_ref: str = "latest",
    ) -> StoredRun:
        runs = self._runs.get((experiment, tool_class), [])
        if not runs:
            raise RunNotFoundError(f"No runs for {tool_class}/{_stem(experiment)}.")
        if run_ref == "latest":
            return runs[-1]
        for stored in runs:
            if stored.run_ref == run_ref:
                return stored
        raise RunNotFoundError(
            f"No run {run_ref!r} for {tool_class}/{_stem(experiment)}."
        )


def _stem(name: str) -> str:
    return name[:-4] if name.endswith(".csv") else name
