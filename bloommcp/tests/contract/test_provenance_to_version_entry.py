"""Provenance maps into a v3 manifest VersionEntry — one provenance path.

Maps the spec "Provenance Maps Into The Manifest VersionEntry" scenario: the
mapping sets the contract-time fields, preserves the v2 fields, and leaves the
per-artifact collections empty (filled by the ResultStore at commit in Tier 2).
No live Supabase, no live write.
"""

from __future__ import annotations

from bloom_mcp.contract import Provenance
from bloom_mcp.storage.schema import CodeVersions, VersionEntry


def _provenance() -> Provenance:
    return Provenance(
        tool="pca_analysis",
        params={"n_components": 3},
        seed=42,
        agent="bloom_agent",
        input_sha256="cd" * 32,
        code_versions=CodeVersions(
            bloommcp="0.1.0",
            sleap_roots_analyze="0.1.0a2",
            sleap_roots_contracts="0.1.0a1",
        ),
        environment="sha256:deadbeef",
        created_at="2026-06-17T00:00:00Z",
        based_on_version="raw",
        outputs={"cleaned": "_cleaned.csv"},
        user_label="run one",
        version_dir="v1_2026-06-17_run_one",
    )


def test_mapping_yields_v3_version_entry_with_contract_fields():
    """to_version_entry sets v3 fields, preserves v2 fields, defers per-artifact."""
    prov = _provenance()
    entry = prov.to_version_entry(version_id="v1")

    assert isinstance(entry, VersionEntry)

    # New v3 contract-time fields.
    assert entry.seed == 42
    assert entry.agent == "bloom_agent"
    assert entry.environment == "sha256:deadbeef"
    assert entry.code_versions.sleap_roots_analyze == "0.1.0a2"
    assert entry.code_versions.sleap_roots_contracts == "0.1.0a1"

    # Preserved v2 fields, with source values.
    assert entry.id == "v1"
    assert entry.created_at == "2026-06-17T00:00:00Z"
    assert entry.tool == "pca_analysis"
    assert entry.params == {"n_components": 3}
    assert entry.based_on_version == "raw"
    assert entry.code_versions.bloommcp == "0.1.0"
    assert entry.outputs == {"cleaned": "_cleaned.csv"}
    assert entry.user_label == "run one"
    assert entry.version_dir == "v1_2026-06-17_run_one"

    # Per-artifact content addressing is filled at commit (Tier 2), not now.
    assert entry.output_sha256 == {}
    assert entry.output_keys == {}
