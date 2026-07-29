"""Old v2/v3 manifests still read under v4 code — the additive bumps hold.

Maps the spec "Additive Manifest Schema v4" back-compat scenarios: a recorded
v2 manifest (string-valued ``outputs``, no v3/v4 fields) and a recorded v3
manifest (no ``source_id``/``source_name``) both validate under v4 code with
``extra="forbid"`` still on, with their absent fields defaulting to unset.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bloom_mcp.manifest.manifest import ManifestSchemaError, validate_schema
from bloom_mcp.manifest.schema import Manifest

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "manifest_v2.json"


def test_recorded_v2_manifest_reads_under_v3():
    """The committed v2 fixture validates and its v3 fields are unset."""
    raw = json.loads(_FIXTURE.read_text())

    validate_schema(raw)  # version 2 <= known (3): accepted
    manifest = Manifest.model_validate(raw)

    assert manifest.manifest_schema_version == 2
    entry = manifest.versions[0]
    # v2 string-valued outputs load unchanged under extra="forbid".
    assert entry.outputs == {"cleaned": "_cleaned.csv", "biplot": "_biplot.png"}
    # v3/v4 fields absent in v2 default to unset.
    assert entry.seed is None
    assert entry.agent is None
    assert entry.environment is None
    assert entry.output_sha256 == {}
    assert entry.output_keys == {}
    assert entry.source_id is None
    assert entry.source_name is None
    # A historical `code_versions.supabase == "unknown"` still reads.
    assert entry.code_versions.supabase == "unknown"


def test_recorded_v3_manifest_reads_under_v4():
    """A v3-shaped manifest (seed/agent/output_sha256, no source_id) still loads."""
    raw = {
        "manifest_schema_version": 3,
        "experiment": {
            "filename": "exp.csv",
            "source_path": "bloommcp_input/exp.csv",
            "input_sha256": "0" * 64,
        },
        "versions": [
            {
                "id": "v1",
                "created_at": "2026-06-17T00:00:00Z",
                "tool": "pca_analysis",
                "params": {"n_components": 3},
                "based_on_version": "raw",
                "code_versions": {"bloommcp": "0.1.0"},
                "outputs": {"loadings": "_loadings.csv"},
                "seed": 42,
                "agent": "bloom_agent",
                "environment": "sha256:deadbeef",
                "output_sha256": {"loadings": "ab" * 32},
                "output_keys": {"loadings": "bloommcp_output/pca_exp/v1/_loadings.csv"},
            }
        ],
        "latest": "v1",
    }

    validate_schema(raw)  # version 3 <= known (4): accepted
    manifest = Manifest.model_validate(raw)

    assert manifest.manifest_schema_version == 3
    entry = manifest.versions[0]
    assert entry.seed == 42
    assert entry.output_sha256 == {"loadings": "ab" * 32}
    # v4 fields absent in v3 default to unset.
    assert entry.source_id is None
    assert entry.source_name is None


def test_newer_schema_version_is_rejected():
    """The version guard rejects a manifest newer than this code understands."""
    with pytest.raises(ManifestSchemaError):
        validate_schema({"manifest_schema_version": 5})
