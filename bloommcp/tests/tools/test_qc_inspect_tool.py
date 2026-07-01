"""Contract + oracle tests for the read-only ``qc_inspect`` tool (Tier 3 / #360).

The turface_19 recommendation oracle + the 5 contract patterns + the read-only
guarantee (a qc_inspect run is never resolved by ``require_clean=True``) + a real-bytes
figure round-trip. The tool delegates ALL EDA to ``sleap_roots_analyze`` and persists a
versioned **report** run under tool class ``qc_inspect`` — no EDA logic in the MCP, and
it produces no cleaned version.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import (
    CleanedVersionRequiredError,
    FakeReader,
    SupabaseReader,
)
from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
from bloom_mcp.tools import _ports
from bloom_mcp.tools import qc_inspect_tool
from bloom_mcp.tools.qc_inspect_tool import (
    QCInspectParams,
    QCInspectResult,
    qc_inspect,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW = _FIXTURES / "turface_19_raw_data.csv"
_GOLDEN = json.loads((_FIXTURES / "turface_19_qc_inspect_golden.json").read_text())

_EXPERIMENT = "turface_19_raw.csv"


def _raw_df() -> pd.DataFrame:
    return pd.read_csv(_RAW)


@pytest.fixture
def injected_ports():
    """FakeReader serving the raw fixture + FakeResultStore, via the _ports seam."""
    reader = FakeReader()
    store = FakeResultStore()
    reader.add_experiment(_EXPERIMENT, _raw_df())
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def _run(**overrides) -> QCInspectResult:
    return qc_inspect(QCInspectParams(experiment=_EXPERIMENT, **overrides))


# ── 2.2 / 2.3 recommendation oracle through the tool ────────────────────────


def test_recommendation_oracle_at_default_params(injected_ports):
    """2.2 — at the canonical defaults the two 15.5%-NaN traits are KEPT and 29 samples
    are dropped; the recommendation drops the traits instead for ZERO sample loss."""
    result = (
        _run()
    )  # canonical defaults (max_nans_per_trait=0.2, max_nans_per_sample=0.0)

    nf = result.per_trait_nan_fraction
    assert nf["Root_Biomass_mg"] == pytest.approx(0.1551, abs=1e-3)
    assert nf["Root_Shoot_Ratio"] == pytest.approx(0.1551, abs=1e-3)
    # Neither trait exceeds the 0.2 default, so the cleanup keeps them...
    assert result.traits_would_be_removed == []
    # ...and dropping their NaN-bearing samples is the uncontrolled loss qc_inspect warns of.
    assert (
        result.samples_lost_at_current_params
        == _GOLDEN["at_default_params"]["samples_lost"]
    )
    assert result.samples_lost_at_current_params == 29

    rec = result.recommendation
    assert rec.no_change_needed is False
    assert (
        sorted(rec.would_remove_traits)
        == _GOLDEN["recommendation"]["would_remove_traits"]
    )
    assert rec.samples_lost_at_recommendation == 0
    assert (
        rec.recommended_max_nans_per_trait < 0.1551
    )  # strictly below the trait NaN fraction
    assert rec.naive_dropna_samples_lost == _GOLDEN["naive_dropna_samples_lost"] == 29


def test_recommendation_tracks_supplied_thresholds(injected_ports):
    """2.3 — at max_nans_per_trait=0.1 the two traits already drop, so no sample is lost
    and no further change is recommended."""
    result = _run(max_nans_per_trait=0.1)
    assert "Root_Biomass_mg" in result.traits_would_be_removed
    assert "Root_Shoot_Ratio" in result.traits_would_be_removed
    assert result.samples_lost_at_current_params == 0
    assert result.recommendation.no_change_needed is True


def test_zero_nan_frame_recommends_no_change():
    """2.4 — a frame with no NaNs yields a no-change recommendation (not a spurious
    lower threshold)."""
    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(16)],
            "geno": ["g1", "g2"] * 8,
            "rep": list(range(16)),
            "t1": [float(i + 1) for i in range(16)],
            "t2": [float(2 * (i + 1)) for i in range(16)],
        }
    )
    reader = FakeReader()
    reader.add_experiment("clean.csv", df)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        result = qc_inspect(QCInspectParams(experiment="clean.csv"))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert result.samples_lost_at_current_params == 0
    assert result.traits_would_be_removed == []
    rec = result.recommendation
    assert rec.no_change_needed is True
    assert rec.recommended_max_nans_per_trait is None
    assert rec.would_remove_traits == []
    assert rec.samples_lost_at_recommendation == 0


def test_all_nan_trait_is_reported_not_rejected():
    """2.5 — an entirely-NaN trait is reported (fraction 1.0, flagged for removal), not
    an error: qc_inspect inspects missingness rather than gating on it."""
    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(16)],
            "geno": ["g1", "g2"] * 8,
            "rep": list(range(16)),
            "t1": [float(i + 1) for i in range(16)],
            "t2": [float(2 * (i + 1)) for i in range(16)],
            "t_allnan": [float("nan")] * 16,
        }
    )
    reader = FakeReader()
    reader.add_experiment("allnan.csv", df)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        result = qc_inspect(
            QCInspectParams(
                experiment="allnan.csv", trait_columns=["t1", "t2", "t_allnan"]
            )
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert result.per_trait_nan_fraction["t_allnan"] == pytest.approx(1.0)
    assert "t_allnan" in result.traits_would_be_removed


# ── 3.1 tools/list presence ─────────────────────────────────────────────────


def test_qc_inspect_appears_in_tools_list_and_siblings_preserved():
    """3.1 — qc_inspect is discoverable; qc_clean + run_qc_workflow are still registered."""
    from fastmcp import Client

    from bloom_mcp import server

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_list())}
    assert "qc_inspect" in tools
    assert tools["qc_inspect"].inputSchema is not None
    assert "qc_clean" in tools  # additive — sibling not removed
    assert "run_qc_workflow" in tools


# ── 3.2 schema round-trip ───────────────────────────────────────────────────


def test_valid_input_output_round_trip(injected_ports):
    result = _run()
    again = QCInspectResult.model_validate(json.loads(result.model_dump_json()))
    assert (
        again.recommendation.would_remove_traits
        == result.recommendation.would_remove_traits
    )


def test_invalid_threshold_is_input_validation_error(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        qc_inspect({"experiment": _EXPERIMENT, "max_nans_per_trait": 1.5})
    assert exc.value.code == "invalid_input"


def test_default_thresholds_mirror_qc_clean_canonical():
    """qc_inspect's defaults must match qc_clean's canonical QC-pipeline defaults so the
    overlays/recommendation reflect the clean a default qc_clean would apply."""
    p = QCInspectParams(experiment="x.csv")
    assert p.max_zeros_per_trait == 0.5
    assert p.max_nans_per_trait == 0.2
    assert p.max_nans_per_sample == 0.0
    assert p.min_samples_per_trait == 10


# ── 3.3 provenance + links (not blobs) ──────────────────────────────────────


def test_provenance_stamped_seed_none_and_links_returned(injected_ports):
    _reader, store = injected_ports
    result = _run()

    stored = store.get_run(_EXPERIMENT, "qc_inspect", "latest")
    assert stored.tool == "qc_inspect"
    assert stored.seed is None  # QC inspection is deterministic — no random_state
    expected = {
        "trait_eda_overview.png",
        "variance_distribution.png",
        "missing_data_pattern.png",
        "nan_samples.csv",
        "recommendation.json",
    }
    assert set(stored.output_keys) == expected

    assert result.run_ref == stored.run_ref
    assert result.manifest_path == stored.manifest_path
    assert set(result.outputs) == expected
    # Links, not blobs: no inline field carries a large payload.
    dumped = result.model_dump()
    assert not any(
        isinstance(v, (list, dict)) and len(str(v)) > 5000 for v in dumped.values()
    )


# ── 3.4 delegation pinning (spy) ────────────────────────────────────────────


def test_delegates_to_analyze_and_never_calls_vendored_cleanup(
    injected_ports, monkeypatch
):
    calls = {"filter": 0, "eda": 0, "inspect": 0}
    real_filter = qc_inspect_tool.apply_data_cleanup_filters
    real_eda = qc_inspect_tool.create_trait_eda_plots
    real_inspect = qc_inspect_tool.inspect_nan_samples

    def _spy_filter(df, trait_cols=None, **kwargs):
        calls["filter"] += 1
        calls["kwargs"] = kwargs
        return real_filter(df, trait_cols, **kwargs)

    def _spy_eda(*a, **k):
        calls["eda"] += 1
        return real_eda(*a, **k)

    def _spy_inspect(df, trait_cols=None, **kwargs):
        calls["inspect"] += 1
        calls["inspect_kwargs"] = kwargs
        return real_inspect(df, trait_cols, **kwargs)

    monkeypatch.setattr(qc_inspect_tool, "apply_data_cleanup_filters", _spy_filter)
    monkeypatch.setattr(qc_inspect_tool, "create_trait_eda_plots", _spy_eda)
    monkeypatch.setattr(qc_inspect_tool, "inspect_nan_samples", _spy_inspect)

    import bloom_mcp.data_cleanup as vendored

    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("qc_inspect called the vendored bloom_mcp.data_cleanup")

    monkeypatch.setattr(vendored, "apply_data_cleanup_filters", _boom)

    _run()

    assert calls["eda"] == 1
    assert calls["inspect"] == 1
    assert calls["filter"] >= 1  # current params (+ once more for the recommendation)
    # Detected roles forwarded to the cleanup + inspection delegates.
    assert calls["kwargs"]["barcode_col"] == "Barcode"
    assert calls["kwargs"]["genotype_col"] == "geno"
    assert calls["kwargs"]["replicate_col"] == "rep"
    assert calls["inspect_kwargs"]["genotype_col"] == "geno"


# ── 3.4b no figure-handle leak + headless backend ───────────────────────────


def test_no_figure_handle_leak_and_agg_backend(injected_ports):
    import matplotlib
    import matplotlib.pyplot as plt

    assert matplotlib.get_backend().lower() == "agg"
    before = len(plt.get_fignums())
    _run()
    assert len(plt.get_fignums()) == before  # every delegate figure was closed


# ── 3.5 role-column fallback (None must not be forwarded) ────────────────────


def test_undetected_role_columns_fall_back_to_delegate_defaults(monkeypatch):
    df = pd.DataFrame(
        {
            "t1": [float(i + 1) for i in range(16)],
            "t2": [float(2 * (i + 1)) for i in range(16)],
        }
    )
    reader = FakeReader()
    reader.add_experiment("roleless.csv", df)
    _ports.configure(reader=reader, store=FakeResultStore())

    captured = {}
    real_filter = qc_inspect_tool.apply_data_cleanup_filters

    def _spy(df_, trait_cols=None, **kwargs):
        captured["kwargs"] = kwargs
        return real_filter(df_, trait_cols, **kwargs)

    monkeypatch.setattr(qc_inspect_tool, "apply_data_cleanup_filters", _spy)
    try:
        qc_inspect(QCInspectParams(experiment="roleless.csv"))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    # None is never forwarded — the kwarg is omitted so the delegate default applies.
    for role in ("genotype_col", "replicate_col", "barcode_col"):
        assert captured["kwargs"].get(role, "x") is not None


# ── 3.6 error envelope ──────────────────────────────────────────────────────


def test_unresolvable_experiment_errors_with_no_run(injected_ports):
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError):
        qc_inspect(QCInspectParams(experiment="does_not_exist.csv"))
    assert store.list_runs("does_not_exist.csv", "qc_inspect") == []


def test_delegate_raise_is_structured_without_leaking(injected_ports, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("secret path /var/secrets/key and host db.internal")

    monkeypatch.setattr(qc_inspect_tool, "apply_data_cleanup_filters", _boom)
    with pytest.raises(BloomMCPError) as exc:
        _run()
    msg = f"{exc.value.message} {exc.value.remedy}"
    assert "secret" not in msg and "/var" not in msg and "db.internal" not in msg


# ── 3.7 trait_columns validation ────────────────────────────────────────────


def test_unknown_trait_column_is_invalid_input_naming_it(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        qc_inspect(
            QCInspectParams(experiment=_EXPERIMENT, trait_columns=["NoSuchTrait"])
        )
    assert exc.value.code == "invalid_input"
    assert "NoSuchTrait" in exc.value.message


def test_non_numeric_trait_column_is_invalid_input(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        qc_inspect(QCInspectParams(experiment=_EXPERIMENT, trait_columns=["geno"]))
    assert exc.value.code == "invalid_input"
    assert "geno" in exc.value.message


# ── 3.8 read-only: structural (no cleaned artifact under tool class qc) ──────


def test_report_run_is_under_qc_inspect_class_with_no_cleaned_artifact(injected_ports):
    _reader, store = injected_ports
    result = _run()
    # Persisted under qc_inspect, never under qc, and never writes a cleaned CSV.
    assert store.list_runs(_EXPERIMENT, "qc") == []
    assert store.list_runs(_EXPERIMENT, "qc_inspect")
    assert not any(name.endswith("_cleaned.csv") for name in result.outputs)


# ── 3.8b read-only over the real resolver (negative composition) ────────────


def test_qc_inspect_run_is_not_resolved_as_cleaned_version(fake_supabase_storage):
    """A committed qc_inspect run must NOT satisfy require_clean=True. Driven through the
    Supabase adapters over the shared in-memory object store — the fakes' reader/store
    are disjoint and cannot exercise the resolver."""
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, _raw_df())
    store = SupabaseResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        _run()
        with pytest.raises(CleanedVersionRequiredError):
            SupabaseReader().load_experiment(_EXPERIMENT, require_clean=True)
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


# ── 3.9 figure-persistence round-trip (real bytes, via the adapters) ────────


def test_persisted_figures_round_trip_as_real_bytes(fake_supabase_storage):
    """The FakeResultStore retains no bytes, so commit through SupabaseResultStore over the
    shared object store and read the stored PNG/JSON bytes back."""
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, _raw_df())
    store = SupabaseResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        _run()
        stored = store.get_run(_EXPERIMENT, "qc_inspect", "latest")
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    png_names = [n for n in stored.output_keys if n.endswith(".png")]
    assert png_names  # at least the trait_eda_overview heat/bar charts
    for name in png_names:
        data = fake_supabase_storage.objects[stored.output_keys[name]]
        assert data[:8] == b"\x89PNG\r\n\x1a\n"  # real PNG, non-empty
        assert hashlib.sha256(data).hexdigest() == stored.output_sha256[name]

    rec_bytes = fake_supabase_storage.objects[stored.output_keys["recommendation.json"]]
    rec = json.loads(rec_bytes.decode("utf-8"))
    assert (
        rec["would_remove_traits"] == _GOLDEN["recommendation"]["would_remove_traits"]
    )
    assert rec["samples_lost_at_recommendation"] == 0


# ── 3.10 second run increments version ──────────────────────────────────────


def test_second_run_increments_version(injected_ports):
    _reader, store = injected_ports
    _run()
    _run()
    assert [r.run_ref for r in store.list_runs(_EXPERIMENT, "qc_inspect")] == [
        "v1",
        "v2",
    ]
    assert store.get_run(_EXPERIMENT, "qc_inspect", "latest").run_ref == "v2"
