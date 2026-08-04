"""Contract-time Provenance round-trips Pydantic <-> JSON exactly.

Maps the spec "Canonical Contract-Time Provenance Record" scenarios: a
fully-populated record round-trips with exact equality (no tolerance — this is
serialization, not recomputation), and per-artifact hashes are empty at contract
time.
"""

from __future__ import annotations

import json

from hypothesis import given, strategies as st

from bloom_mcp.contract import Provenance
from bloom_mcp.manifest.schema import CodeVersions


def _make(**overrides) -> Provenance:
    base = dict(
        tool="pca_analysis",
        params={"n_components": 3, "svd_solver": "full"},
        seed=42,
        agent="bloom_agent",
        input_sha256="ab" * 32,
        code_versions=CodeVersions(bloommcp="0.1.0"),
        environment="sha256:deadbeef",
        created_at="2026-06-17T00:00:00Z",
    )
    base.update(overrides)
    return Provenance(**base)


def test_full_record_roundtrips_exactly():
    """A fully-populated contract-time Provenance survives JSON unchanged."""
    prov = _make()
    again = Provenance.model_validate(json.loads(prov.model_dump_json()))
    assert again == prov


def test_per_artifact_hashes_empty_at_contract_time():
    """The decorator stamps no per-artifact hashes/keys before delegation."""
    prov = _make()
    assert prov.output_sha256 == {}
    assert prov.output_keys == {}
    assert prov.outputs == {}


def test_source_id_and_name_default_to_none():
    """A stamped Provenance carries no source identity until create_run sets it."""
    prov = _make()
    assert prov.source_id is None
    assert prov.source_name is None


def test_source_id_and_name_roundtrip_exactly():
    """A Provenance carrying source_id/source_name survives JSON unchanged."""
    prov = _make(source_id=7, source_name="reprocess-2026-07")
    again = Provenance.model_validate(json.loads(prov.model_dump_json()))
    assert again == prov
    assert again.source_id == 7
    assert again.source_name == "reprocess-2026-07"


@given(
    seed=st.integers(min_value=0, max_value=2**32 - 1),
    environment=st.one_of(st.none(), st.text(min_size=1, max_size=40)),
    params=st.dictionaries(st.text(min_size=1, max_size=8), st.integers(), max_size=4),
)
def test_roundtrip_property(seed, environment, params):
    """Round-trip equality holds across varied seed/environment/params."""
    prov = _make(seed=seed, environment=environment, params=params)
    again = Provenance.model_validate(json.loads(prov.model_dump_json()))
    assert again == prov
