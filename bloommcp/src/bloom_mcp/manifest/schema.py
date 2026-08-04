"""Pydantic models for manifest.json (schema version 5).

Every (experiment, tool_class) pair has one manifest.json in the
bloommcp-data bucket listing all its runs. These models define what
goes in it.

Schema version 3 was an **additive** bump over v2: it added optional provenance
fields (`seed`, `agent`, `environment`) and per-artifact content-addressing
(`output_sha256`, `output_keys`) alongside the retained v2 `outputs` string map,
and extended `code_versions` with `sleap-roots-analyze` / `sleap-roots-contracts`.

Schema version 4 was an **additive** bump over v3: it added optional `source_id`/
`source_name` fields to `VersionEntry`, identifying which Bloom database source
(a `cyl_trait_sources` row) a DB-backed raw read resolved — the replacement
identity signal for a read that no longer has an on-disk path to
content-address via `RawSourced` (see `bloom_mcp.data_access.SourceSelectable`).

Schema version 5 is an **additive** bump over v4: it adds an optional
`storage_backend` field to `Manifest`, stamped with the active
`BLOOM_STORAGE_BACKEND` name (`supabase` or `local`) on every write. This is
the backend-mixing sentinel from #395: because the `supabase` and `local`
backends each own a physically disjoint manifest, this field records which
backend most recently wrote a given catalog, making a mixed-backend history
split observable by inspecting the file directly (see
`bloommcp/docs/storage-backends.md`).

Every new field across all three bumps is optional, so previously-written v2,
v3, and v4 manifests still validate and read without error (see
`tests/contract/test_v2_backcompat.py`).

Strict mode is on: passing an unknown field raises a ValidationError
instead of being silently accepted into the file.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

CURRENT_SCHEMA_VERSION = 5


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
    # Input-contract validation findings, recorded by ``qc_clean`` (#403). Optional
    # and additive within schema v3: absent on runs that don't validate their input
    # (and on all pre-#403 manifests), so those still load under ``extra="forbid"``.
    # Keys: ``mode``, ``contract_version``, ``resolved_roles``, ``excluded_columns``,
    # ``warnings``.
    input_validation: Optional[dict] = None
    # --- v4 additive (all optional → v2/v3 manifests still validate) ---
    # Which DB source/pipeline-run backed the experiment read this run consumed,
    # when the active ExperimentReader is SourceSelectable. Absent for reads with
    # no source-versioned substrate (FakeReader, LocalReader) or legacy DB data
    # with no tracked source_id.
    source_id: Optional[int] = None
    source_name: Optional[str] = None


class Manifest(_StrictModel):
    """Top-level manifest.json schema."""

    manifest_schema_version: int = Field(default=CURRENT_SCHEMA_VERSION)
    experiment: ExperimentBlock
    versions: list[VersionEntry] = Field(default_factory=list)
    latest: Optional[str] = None
    # --- v5 additive (optional → v2/v3/v4 manifests still validate) ---
    # Which object-storage backend (`supabase` or `local`) most recently wrote
    # this manifest — the #395 backend-mixing sentinel. Absent on manifests
    # written before this field existed.
    storage_backend: Optional[str] = None
