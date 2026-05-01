"""Per-experiment, per-tool-class directory abstraction (read-only in Phase A)."""
import hashlib
from pathlib import Path

from .manifest import read_manifest

_HASH_CHUNK_BYTES = 1024 * 1024


class AnalysisDir:
    """Wraps <output_root>/<tool_class>_<stem>/ and its manifest."""

    def __init__(
        self,
        output_root: Path,
        experiment_filename: str,
        tool_class: str,
    ) -> None:
        self.output_root = Path(output_root)
        self.experiment_filename = experiment_filename
        self.tool_class = tool_class
        self.stem = Path(experiment_filename).stem
        self.path = self.output_root / f"{tool_class}_{self.stem}"
        self._cached_input_sha256: str | None = None

    def read_manifest(self) -> dict | None:
        if not self.path.exists():
            return None
        return read_manifest(self.path)

    def list_versions(self) -> list[dict]:
        """All version entries sorted by created_at; empty list if no manifest."""
        manifest = self.read_manifest()
        if not manifest:
            return []
        versions = list(manifest.get("versions", []))
        return sorted(versions, key=lambda v: v.get("created_at", ""))

    def get_version(self, version_id: str) -> dict | None:
        """Resolve a version by id, or "latest" via the manifest's latest pointer."""
        manifest = self.read_manifest()
        if not manifest:
            return None
        target_id = manifest.get("latest") if version_id == "latest" else version_id
        if not target_id:
            return None
        for entry in manifest.get("versions", []):
            if entry.get("id") == target_id:
                return entry
        return None

    def input_sha256(self, source_csv: Path) -> str:
        """Stream-hash the source CSV; cached on the instance after first call."""
        if self._cached_input_sha256 is not None:
            return self._cached_input_sha256
        h = hashlib.sha256()
        with open(source_csv, "rb") as f:
            for chunk in iter(lambda: f.read(_HASH_CHUNK_BYTES), b""):
                h.update(chunk)
        self._cached_input_sha256 = h.hexdigest()
        return self._cached_input_sha256
