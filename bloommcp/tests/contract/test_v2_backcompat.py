"""Old v2 manifests still read under v3 code — the additive bump holds.

Maps the spec "Additive Manifest Schema v3" back-compat scenario: a recorded v2
manifest (string-valued ``outputs``, no v3 fields) validates under v3 code with
``extra="forbid"`` still on, and its v3 fields default to unset.
"""

from __future__ import annotations

import json
from pathlib import Path

from bloom_mcp.storage.manifest import validate_schema
from bloom_mcp.storage.schema import Manifest

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
    # v3 fields absent in v2 default to unset.
    assert entry.seed is None
    assert entry.agent is None
    assert entry.environment is None
    assert entry.output_sha256 == {}
    assert entry.output_keys == {}
