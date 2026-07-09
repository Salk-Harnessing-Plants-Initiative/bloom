"""AnalysisWriter — versioned writes from a tmp staging dir to Supabase Storage.

WARNING: bloommcp deploys as ONE container per env with ONE FastMCP
process per container. This writer assumes single-writer-per-env: there
is no `fcntl.flock` and no compare-and-swap on the manifest. If a second
writer races a `create_version` → `commit` pair, both may allocate the
same `v<N>` and the second commit silently clobbers the first's manifest
entry. Don't run two bloommcp instances against the same Supabase project.
"""

import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from bloom_mcp.supabase_client import upload_file

from .analysis_dir import AnalysisDir
from .code_versions import get_code_versions
from .manifest import write_manifest
from .schema import ExperimentBlock, Manifest, VersionEntry
from .versioning import next_version_id, version_dir_name


class AnalysisWriter:
    """Owns the write side of one tool's run on one experiment.

    Lifecycle: construct → `create_version()` → tool writes outputs into
    the returned tmp staging directory → `commit({...})` uploads the
    staged files and the updated manifest to Supabase Storage, then
    removes the staging dir. Each instance commits exactly once.

    Single-writer assumption (see module docstring) — no `fcntl.flock`,
    no ETag CAS on the manifest.
    """

    def __init__(
        self,
        output_root,
        experiment_filename: str,
        tool_class: str,
        source_csv: Optional[Path] = None,
    ) -> None:
        self.analysis_dir = AnalysisDir(output_root, experiment_filename, tool_class)
        self.source_csv = source_csv
        self._pending_version_id: Optional[str] = None
        self._pending_version_dir_name: Optional[str] = None
        self._pending_staging_dir: Optional[Path] = None
        self._pending_tool_name: Optional[str] = None
        self._pending_params: Optional[dict] = None
        self._pending_user_label: Optional[str] = None

    @property
    def version_id(self) -> Optional[str]:
        """The currently-pending version id (e.g. `"v3"`), or None if
        `create_version()` hasn't been called yet. Use this when a tool
        needs the slug before `commit()` (e.g. for plot filenames)."""
        return self._pending_version_id

    def create_version(
        self,
        tool_name: str,
        params: dict,
        user_label: Optional[str] = None,
    ) -> Path:
        """Allocate the next `v<N>`, return a tmp staging directory.

        The tool writes outputs into the returned Path using normal `Path`
        operations. `commit()` then walks the staging dir and uploads
        each file to its key under the version's storage prefix.
        """
        manifest = self.analysis_dir.read_manifest()
        version_id = next_version_id(manifest)
        dir_name = version_dir_name(version_id, user_label)
        staging_dir = Path(tempfile.mkdtemp(prefix="bloommcp_v_"))

        self._pending_version_id = version_id
        self._pending_version_dir_name = dir_name
        self._pending_staging_dir = staging_dir
        self._pending_tool_name = tool_name
        self._pending_params = dict(params)
        self._pending_user_label = user_label
        return staging_dir

    def commit(self, outputs: dict[str, str]) -> VersionEntry:
        """Upload staged files + manifest; remove staging dir.

        `outputs` maps a stable short name (e.g. `"cleaned"`) to a path
        relative to the staging directory (e.g. `"_cleaned.csv"`).
        """
        if self._pending_version_id is None:
            raise RuntimeError("commit() called before create_version()")

        sha = ""
        if self.source_csv is not None and self.source_csv.exists():
            sha = self.analysis_dir.input_sha256(self.source_csv)

        version_prefix_rel = self._pending_version_dir_name
        for _, rel_path in outputs.items():
            local = self._pending_staging_dir / rel_path
            key = self.analysis_dir.key(f"{version_prefix_rel}/{rel_path}")
            upload_file(key, local)

        entry = VersionEntry(
            id=self._pending_version_id,
            created_at=datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z"),
            tool=self._pending_tool_name,
            params=self._pending_params,
            based_on_version="raw",
            code_versions=get_code_versions(),
            outputs=dict(outputs),
            user_label=self._pending_user_label,
            version_dir=self._pending_version_dir_name,
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
            write_manifest(self.analysis_dir.path, manifest)
        finally:
            shutil.rmtree(self._pending_staging_dir, ignore_errors=True)
            self._pending_version_id = None
            self._pending_version_dir_name = None
            self._pending_staging_dir = None
        return entry

    def __del__(self):
        """Best-effort cleanup of an orphaned staging dir if commit didn't run."""
        staging = getattr(self, "_pending_staging_dir", None)
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
