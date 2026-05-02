"""AnalysisWriter — atomic, versioned writes to a per-experiment, per-tool-class directory."""
import fcntl
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Optional

from .analysis_dir import AnalysisDir
from .code_versions import get_code_versions
from .manifest import write_manifest_atomic
from .schema import (
    CodeVersions,
    ExperimentBlock,
    Manifest,
    VersionEntry,
)
from .versioning import next_version_id, version_dir_name


class AnalysisWriter:
    """Owns the write side of one tool's run on one experiment.

    Lifecycle: construct → create_version() → tool writes outputs into the
    returned directory → commit({...}) appends the manifest entry atomically.
    Each instance commits exactly once.

    Concurrency: create_version() acquires an fcntl.flock on the experiment
    directory and holds it until commit() completes, serializing concurrent
    writers (in-process threads or cross-process). On POSIX only — bloommcp
    runs on Linux containers, dev on macOS, both POSIX.
    """

    def __init__(
        self,
        output_root: Path,
        experiment_filename: str,
        tool_class: str,
        source_csv: Optional[Path] = None,
    ) -> None:
        self.analysis_dir = AnalysisDir(output_root, experiment_filename, tool_class)
        self.source_csv = source_csv
        self._pending_version_id: Optional[str] = None
        self._pending_version_dir: Optional[Path] = None
        self._pending_tool_name: Optional[str] = None
        self._pending_params: Optional[dict] = None
        self._pending_user_label: Optional[str] = None
        self._lock_handle: Optional[IO] = None

    def _acquire_lock(self) -> None:
        self.analysis_dir.path.mkdir(parents=True, exist_ok=True)
        lock_path = self.analysis_dir.path / ".write_lock"
        self._lock_handle = open(lock_path, "w")
        fcntl.flock(self._lock_handle, fcntl.LOCK_EX)

    def _release_lock(self) -> None:
        if self._lock_handle is not None:
            try:
                fcntl.flock(self._lock_handle, fcntl.LOCK_UN)
            finally:
                self._lock_handle.close()
                self._lock_handle = None

    def create_version(
        self,
        tool_name: str,
        params: dict,
        user_label: Optional[str] = None,
    ) -> Path:
        """Allocate the next v<N>, create the directory, return the path."""
        self._acquire_lock()
        try:
            manifest = self.analysis_dir.read_manifest()
            version_id = next_version_id(manifest)
            dir_name = version_dir_name(version_id, user_label)
            version_dir = self.analysis_dir.path / dir_name
            version_dir.mkdir(parents=True, exist_ok=False)
        except Exception:
            self._release_lock()
            raise

        self._pending_version_id = version_id
        self._pending_version_dir = version_dir
        self._pending_tool_name = tool_name
        self._pending_params = dict(params)  # defensive copy
        self._pending_user_label = user_label
        return version_dir

    def commit(self, outputs: dict[str, str]) -> VersionEntry:
        """Append the manifest entry atomically. outputs maps short name → relative path."""
        if self._pending_version_id is None:
            raise RuntimeError("commit() called before create_version()")

        sha = ""
        if self.source_csv is not None and self.source_csv.exists():
            sha = self.analysis_dir.input_sha256(self.source_csv)

        entry = VersionEntry(
            id=self._pending_version_id,
            created_at=datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            tool=self._pending_tool_name,
            params=self._pending_params,
            based_on_version="raw",
            code_versions=get_code_versions(),
            outputs=dict(outputs),
            user_label=self._pending_user_label,
        )

        existing = self.analysis_dir.read_manifest()
        if existing is None:
            manifest = Manifest(
                experiment=ExperimentBlock(
                    filename=self.analysis_dir.experiment_filename,
                    source_path=str(self.source_csv) if self.source_csv else "",
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

        try:
            write_manifest_atomic(self.analysis_dir.path, manifest)
        finally:
            # Reset pending state so a fresh create_version + commit pair can reuse the writer
            self._pending_version_id = None
            self._pending_version_dir = None
            self._release_lock()
        return entry
