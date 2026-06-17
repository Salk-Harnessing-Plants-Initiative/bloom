"""Per-experiment, per-tool-class storage prefix abstraction."""

import hashlib
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .manifest import read_manifest
from .schema import Manifest, VersionEntry

_HASH_CHUNK_BYTES = 1024 * 1024


class AnalysisDir:
    """Wraps the storage prefix `<output_prefix>/<tool_class>_<stem>/` and its manifest catalog.

    `output_prefix` is the logical prefix inside the `bloommcp-data` bucket
    (typically `bloommcp_output`). For migration ergonomics it also accepts
    a `Path` — the value is normalised to a slash-trimmed string before use,
    so existing callers that still pass `BLOOM_OUTPUT_DIR` keep working
    until they are updated.
    """

    def __init__(
        self,
        output_root,
        experiment_filename: str,
        tool_class: str,
    ) -> None:
        self.output_root = str(output_root).rstrip("/")
        self.experiment_filename = experiment_filename
        self.tool_class = tool_class
        self.stem = Path(experiment_filename).stem
        self.path = f"{self.output_root}/{tool_class}_{self.stem}/"
        self._cached_input_sha256: Optional[str] = None

    def key(self, rel: str) -> str:
        """Compose a full object key from a path relative to this analysis dir.

        Example: `dir.key("v1_2026-06-05/_cleaned.csv")` →
        `bloommcp_output/qc_my_exp/v1_2026-06-05/_cleaned.csv`.
        """
        return f"{self.path}{rel.lstrip('/')}"

    def read_manifest(self) -> Optional[Manifest]:
        """Return the manifest at `<path>/manifest.json`, or None if absent."""
        return read_manifest(self.path)

    def list_versions(self) -> list[VersionEntry]:
        """All version entries sorted by created_at; empty list if no manifest."""
        manifest = self.read_manifest()
        if manifest is None:
            return []
        return sorted(manifest.versions, key=lambda v: v.created_at)

    def get_version(self, version_id: str) -> Optional[VersionEntry]:
        """Resolve a version by id, or `latest` via the manifest's latest pointer."""
        manifest = self.read_manifest()
        if manifest is None:
            return None
        target_id = manifest.latest if version_id == "latest" else version_id
        if not target_id:
            return None
        for entry in manifest.versions:
            if entry.id == target_id:
                return entry
        return None

    def input_sha256(self, source_csv: Path) -> str:
        """Stream-hash the source CSV; cached on the instance after first call.

        Source CSVs are local (bind-mounted via SLEAP_OUT_CSV); the migration
        only moves analysis outputs to Supabase Storage, not raw inputs.
        """
        if self._cached_input_sha256 is not None:
            return self._cached_input_sha256
        h = hashlib.sha256()
        with open(source_csv, "rb") as f:
            for chunk in iter(lambda: f.read(_HASH_CHUNK_BYTES), b""):
                h.update(chunk)
        self._cached_input_sha256 = h.hexdigest()
        return self._cached_input_sha256

    @contextmanager
    def with_snapshot(self) -> Iterator[Optional[Manifest]]:
        """Pin the manifest at entry; callers in the context see this snapshot."""
        snapshot = self.read_manifest()
        try:
            yield snapshot
        finally:
            pass
