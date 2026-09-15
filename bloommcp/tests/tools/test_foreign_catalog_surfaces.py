"""#573 review (finding 2a): every direct `load_experiment_data`/`load_frame`
consumer surfaces the typed foreign-catalog error, not a flattened generic.

The envelope-declaring analysis tools are covered end-to-end in
`test_local_mode.py`; these are the previously uncovered surfaces — the plain
string-returning viz tools (bare `except Exception` used to swallow the typed
message), the `summarize_trait` envelope tool (used to answer `invalid_input`
with a pick-another-experiment remedy), and the core `load_experiment_data`
discovery tool.
"""

from __future__ import annotations

import json

import pytest

from bloom_mcp.contract import BloomMCPError, Provenance
from bloom_mcp.result_store import SupabaseResultStore

_EXPERIMENT = "turface_19.csv"
_MANIFEST_KEY = "bloommcp_output/qc_turface_19/manifest.json"


@pytest.fixture(autouse=True)
def _reset_backend_state():
    """Backend memo + #573 sticky-flag hygiene (see test_supabase_result_store)."""
    import bloom_mcp.storage_backend as _sb

    _sb.reset_backend_for_tests()
    yield
    _sb.reset_backend_for_tests()


@pytest.fixture
def foreign_qc_catalog(fake_supabase_storage, monkeypatch):
    """A committed qc catalog whose sentinel is hand-patched foreign."""
    monkeypatch.delenv("BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST", raising=False)
    store = SupabaseResultStore()
    run = store.create_run(
        experiment=_EXPERIMENT,
        tool_class="qc",
        provenance=Provenance.stamp(tool="qc_clean", params={}),
    )
    (run.staging_dir / "_cleaned.csv").write_text("Genotype,trait_a\ng1,1.0\n")
    store.commit(run, {"_cleaned.csv": "_cleaned.csv"})
    raw = json.loads(fake_supabase_storage.objects[_MANIFEST_KEY])
    assert raw["storage_backend"] == "supabase"
    raw["storage_backend"] = "local"
    fake_supabase_storage.objects[_MANIFEST_KEY] = json.dumps(raw).encode()
    return fake_supabase_storage


def test_viz_tool_returns_the_typed_message_not_the_generic_flatten(
    foreign_qc_catalog,
):
    from bloom_mcp.sections.sleap_roots.analysis.plot_trait_histograms import (
        plot_trait_histograms,
    )

    out = plot_trait_histograms(_EXPERIMENT)

    assert "'local'" in out and "'supabase'" in out
    assert "could not be read" not in out
    assert "ALLOW_FOREIGN_MANIFEST" not in out  # no bypass advertisement


def test_summarize_trait_surfaces_tool_error_with_do_not_retry_remedy(
    foreign_qc_catalog,
):
    from bloom_mcp.sections.phenotyping_segmentation.summarize_trait import (
        SummarizeTraitParams,
        summarize_trait,
    )

    with pytest.raises(BloomMCPError) as exc:
        summarize_trait(SummarizeTraitParams(experiment=_EXPERIMENT, trait="trait_a"))

    err = exc.value
    assert err.code == "tool_error"  # not invalid_input, not internal_error
    assert "'local'" in err.message and "'supabase'" in err.message
    assert "list_available_experiments" not in err.remedy  # wrong old remedy
    assert "retry" not in err.remedy.lower()


def test_core_load_experiment_data_returns_the_typed_message(foreign_qc_catalog):
    from bloom_mcp.sections.core.load_experiment_data import load_experiment_data

    out = load_experiment_data(_EXPERIMENT)

    assert "'local'" in out and "'supabase'" in out
    assert "could not be read" not in out
