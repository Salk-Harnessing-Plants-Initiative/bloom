"""Pydantic models for manifest.json (schema version 3).

Every (experiment, tool_class) pair has one manifest.json in the
bloommcp-data bucket listing all its runs. These models define what
goes in it.

Schema version 3 is an **additive** bump over v2: it adds optional provenance
fields (`seed`, `agent`, `environment`) and per-artifact content-addressing
(`output_sha256`, `output_keys`) alongside the retained v2 `outputs` string map,
and extends `code_versions` with `sleap-roots-analyze` / `sleap-roots-contracts`.
Every new field is optional, so previously-written v2 manifests still validate
and read without error (see `tests/contract/test_v2_backcompat.py`).

Strict mode is on: passing an unknown field raises a ValidationError
instead of being silently accepted into the file.
"""

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

CURRENT_SCHEMA_VERSION = 3


class _StrictModel(BaseModel):
    """Common base: forbid unknown fields so writer bugs raise loudly. Pydantic check"""

    model_config = ConfigDict(extra="forbid")


class CodeVersions(_StrictModel):
    """Installed package versions captured at write time for provenance.

    Useful to track which version of the code generated the output in the wrote
    folder. Every field is **installed-only**: a version is recorded only for an
    actually pip-installed distribution; an absent distribution is omitted (left
    `None`) rather than recorded as the literal `"unknown"` (which is noise).
    """

    bloommcp: Optional[str] = None
    supabase: Optional[str] = None
    sleap_roots_analyze: Optional[str] = None
    sleap_roots_contracts: Optional[str] = None


class ExperimentBlock(_StrictModel):
    """Identifies the experiment whose analyses this manifest catalogs."""

    filename: str
    source_path: str
    input_sha256: str


class VersionEntry(_StrictModel):
    """Every time a tool runs and commits a file, a new version of result is noted on the manifest file.

    'id' is "v<N>" for the Nth run on this experiment.

    Schema-v3 additive fields: `seed` (resolved random_state) and `agent`/actor
    for reproducibility provenance; `environment` for the exact-environment
    pointer; and the per-artifact `output_sha256` / `output_keys` sibling maps
    (keyed by the same logical output name as `outputs`) for content addressing.
    The v2 `outputs: dict[str, str]` field is retained unchanged so v2 manifests
    still load under `extra="forbid"`.
    """

    id: str
    created_at: str
    tool: str
    params: dict
    based_on_version: str
    code_versions: CodeVersions
    outputs: dict[str, str]
    user_label: Optional[str] = None
    version_dir: str = ""
    # --- v3 additive (all optional → v2 manifests still validate) ---
    seed: Optional[int] = None
    agent: Optional[str] = None
    environment: Optional[str] = None
    output_sha256: dict[str, str] = Field(default_factory=dict)
    output_keys: dict[str, str] = Field(default_factory=dict)


class Manifest(_StrictModel):
    """Top-level manifest.json schema."""

    manifest_schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)
    experiment: ExperimentBlock
    versions: list[VersionEntry] = Field(default_factory=list)
    latest: Optional[str] = None
