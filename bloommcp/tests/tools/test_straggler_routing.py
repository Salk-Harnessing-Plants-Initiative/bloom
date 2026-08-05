"""qc_clean provenance: source_csv sourced from the active reader.

correlation_tools (formerly covered here) is deleted (#438, C9) — its absence
is asserted in test_devendor_invariants.py. The run_qc_workflow-based
provenance regression test (formerly here) is superseded by the test below,
which covers the same regression through qc_clean directly — run_qc_workflow
and tools/workflows/ are retired (#438), also asserted in
test_devendor_invariants.py.
"""

from __future__ import annotations

import hashlib
import json

import bloom_mcp.experiment_utils as eu
import bloom_mcp.storage_backend as sb
import pytest

from bloom_mcp.result_store import SupabaseResultStore
from bloom_mcp.tools import _ports


@pytest.fixture
def reset_ports():
    """Restore _ports reader/store after a test — avoids constructing new
    SupabaseReader() in teardown (which could raise if validation tightens).
    Also resets the memoized storage backend so the next test starts clean."""
    prev_reader = _ports.reader()
    prev_store = _ports.store()
    yield
    _ports.configure(reader=prev_reader, store=prev_store)
    sb.reset_backend_for_tests()


def test_qc_clean_sources_provenance_from_experiment_local_root(
    monkeypatch, tmp_path, reset_ports
):
    """qc_clean self-stamps provenance and now sources source_csv through the active
    reader (``_ports.raw_source_for``), so a custom ``BLOOM_EXPERIMENT_LOCAL_ROOT``
    distinct from ``TRAITS_DIR`` is honoured — the committed manifest content-addresses
    the real input instead of silently recording an empty ``input_sha256``.

    Regression for the pre-fix hard-code (``local_src = TRAITS_DIR / experiment``):
    with the two roots divergent, that path resolved under the *empty* TRAITS_DIR,
    found nothing, and dropped the hash. TRAITS_DIR is monkeypatched to an empty dir
    so a reintroduced hard-code fails here.
    """
    from bloom_mcp.data_access import LocalReader
    from bloom_mcp.sections.sleap_roots.analysis.qc_clean import (
        QCCleanParams,
        qc_clean,
    )

    exp_root = tmp_path / "experiments"  # BLOOM_EXPERIMENT_LOCAL_ROOT
    exp_root.mkdir()
    traits_dir = tmp_path / "traits"  # TRAITS_DIR — deliberately empty + divergent
    traits_dir.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    # plant_id is a recognized SAMPLE_ID_PATTERNS name (#403) so qc_clean's
    # traceability requirement auto-detects it without a role override.
    rows = "".join(f"g{i},p{i},{float(i)},{float(i + 1)}\n" for i in range(12))
    (exp_root / "exp.csv").write_text("Genotype,plant_id,trait_a,trait_b\n" + rows)

    monkeypatch.setattr(eu, "TRAITS_DIR", traits_dir)
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(store))
    monkeypatch.setenv("BLOOM_EXPERIMENT_LOCAL_ROOT", str(exp_root))
    monkeypatch.setenv("BLOOM_STORAGE_URL", "http://localhost/output")
    sb.reset_backend_for_tests()
    _ports.configure(reader=LocalReader(), store=SupabaseResultStore())

    qc_clean(QCCleanParams(experiment="exp.csv"))

    manifest = json.loads(
        (store / "bloommcp_output" / "qc_exp" / "manifest.json").read_bytes()
    )
    expected = hashlib.sha256((exp_root / "exp.csv").read_bytes()).hexdigest()
    assert manifest["experiment"]["input_sha256"] == expected
