"""Pydantic models for manifest.json (schema version 2).

Every (experiment, tool_class) pair has one manifest.json in the
bloommcp-data bucket listing all its runs. These models define what
goes in it.


Strict mode is on: passing an unknown field raises a ValidationError
instead of being silently accepted into the file.
"""
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

CURRENT_SCHEMA_VERSION = 2


class _StrictModel(BaseModel):
    """Common base: forbid unknown fields so writer bugs raise loudly. Pydantic check"""
    model_config = ConfigDict(extra="forbid")


class CodeVersions(_StrictModel):
    """Installed package versions captured at write time for provenance.
        Useful to track which version of the code generated the output in the wrote folder.
    """
    bloommcp: str
    supabase: str = "unknown"


class ExperimentBlock(_StrictModel):
    """Identifies the experiment whose analyses this manifest catalogs."""
    filename: str
    source_path: str
    input_sha256: str


class VersionEntry(_StrictModel):
    """Every time a tool runs and commits a file, a new versio of result is noted on the manifest file.
    'id' is "v<N>" for the Nth run on this experiment.
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
