"""Pydantic schema for manifest.json (manifest_schema_version: 1).

Each per-experiment, per-tool-class directory (e.g. BLOOM_OUTPUT_DIR/qc_<stem>/)
contains exactly one manifest.json that catalogs every prior analysis run on
that experiment. The models below are the canonical shape of that file at
schema version 1; any future change to the shape MUST bump the version
constant in manifest.py and add a migration path.

Models are strict by default (extra fields rejected) so writer bugs that emit
unexpected keys surface as ValidationError at commit time rather than silently
corrupting the on-disk catalog.
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

CURRENT_SCHEMA_VERSION = 1


class _StrictModel(BaseModel):
    """Common base: forbid unknown fields so writer bugs raise loudly."""
    model_config = ConfigDict(extra="forbid")


class CodeVersions(_StrictModel):
    """Installed package versions captured at write time for provenance."""
    bloommcp: str
    sleap_roots_analyze: str


class ExperimentBlock(_StrictModel):
    """Identifies the experiment whose analyses this manifest catalogs."""
    filename: str
    source_path: str
    input_sha256: str


class VersionEntry(_StrictModel):
    """One analysis run recorded in the manifest's versions list.

    `id` is "v<N>" for normal runs, or "v0_legacy" for the synthesised entry
    written by the on-startup migration. `outputs` maps a stable short name
    (e.g. "_cleaned.csv") to its path relative to the experiment-class
    directory.
    """
    id: str
    created_at: str
    tool: str
    params: dict
    based_on_version: str
    code_versions: CodeVersions
    outputs: dict[str, str]
    user_label: Optional[str] = None


class Manifest(_StrictModel):
    """Top-level manifest.json schema."""
    manifest_schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)
    experiment: ExperimentBlock
    versions: list[VersionEntry] = Field(default_factory=list)
    latest: Optional[str] = None
