"""Manifest schema v3: new entries are v3 and v3 entries round-trip.

Maps the spec "Additive Manifest Schema v3" forward-direction scenarios.
"""

from __future__ import annotations

from bloom_mcp.storage.schema import (
    CURRENT_SCHEMA_VERSION,
    CodeVersions,
    ExperimentBlock,
    Manifest,
    VersionEntry,
)


def _v3_entry() -> VersionEntry:
    return VersionEntry(
        id="v2",
        created_at="2026-06-17T00:00:00Z",
        tool="pca_analysis",
        params={"n_components": 3},
        based_on_version="raw",
        code_versions=CodeVersions(bloommcp="0.1.0", sleap_roots_analyze="0.1.0a2"),
        outputs={"loadings": "_loadings.csv"},
        output_sha256={"loadings": "ab" * 32},
        output_keys={"loadings": "bloommcp_output/pca_turface/v2/_loadings.csv"},
        seed=42,
        agent="bloom_agent",
        environment="sha256:deadbeef",
        user_label="run two",
        version_dir="v2_2026-06-17_run_two",
    )


def test_current_schema_version_is_3():
    """The schema constant and a fresh manifest both report version 3."""
    assert CURRENT_SCHEMA_VERSION == 3
    manifest = Manifest(
        experiment=ExperimentBlock(
            filename="x.csv", source_path="bloommcp_input/x.csv", input_sha256="0" * 64
        )
    )
    assert manifest.manifest_schema_version == 3


def test_v3_version_entry_roundtrips_exactly():
    """A v3 entry with the new sibling collections round-trips through JSON."""
    entry = _v3_entry()
    again = VersionEntry.model_validate(entry.model_dump(mode="json"))
    assert again == entry
