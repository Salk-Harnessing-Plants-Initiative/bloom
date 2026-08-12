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
from bloom_mcp.sections.sleap_roots.analysis import qc_inspect as qc_inspect_tool
from bloom_mcp.sections.sleap_roots.analysis.qc_inspect import (
    QCInspectParams,
    QCInspectResult,
    qc_inspect,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW = _FIXTURES / "turface_19_raw_data.csv"
_GOLDEN = json.loads(
    (_FIXTURES / "turface_19_qc_inspect_golden.json").read_text(encoding="utf-8")
)

_EXPERIMENT = "turface_19_raw.csv"


def _raw_df() -> pd.DataFrame:
    return pd.read_csv(_RAW, encoding="utf-8")


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
    # No non-finite values in turface; residual NaN in kept cols is 0 at the defaults.
    assert result.per_trait_inf_count == {}
    assert (
        result.residual_nan_cells_at_current_params
        == _GOLDEN["at_default_params"]["residual_nan_cells"]
        == 0
    )

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
    # Pin the golden's headline value exactly (floor of 15.51% to the 0.01 step below).
    assert (
        rec.recommended_max_nans_per_trait
        == _GOLDEN["recommendation"]["recommended_max_nans_per_trait"]
        == 0.15
    )
    # Per-offending-trait missingness footprint: each 15.5%-NaN trait touches 29 samples.
    assert rec.offending_trait_nan_counts == {
        "Root_Biomass_mg": 29,
        "Root_Shoot_Ratio": 29,
    }
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
    """3.1 — qc_inspect is discoverable; qc_clean is still registered."""
    from fastmcp import Client

    from bloom_mcp import server

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_list())}
    assert "sleap_roots_qc_inspect" in tools
    assert tools["sleap_roots_qc_inspect"].inputSchema is not None
    assert "sleap_roots_qc_clean" in tools  # additive — sibling not removed


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
    overlays/recommendation reflect the clean a default qc_clean would apply. Compared
    field-by-field against QCCleanParams (not just literals) so a future qc_clean bump that
    isn't mirrored here fails — the thresholds are single-sourced in _qc_shared."""
    from bloom_mcp.sections.sleap_roots.analysis.qc_clean import QCCleanParams

    qi = QCInspectParams(experiment="x.csv")
    qc = QCCleanParams(experiment="x.csv")
    assert qi.max_zeros_per_trait == qc.max_zeros_per_trait == 0.5
    assert qi.max_nans_per_trait == qc.max_nans_per_trait == 0.2
    assert qi.max_nans_per_sample == qc.max_nans_per_sample == 0.0
    assert qi.min_samples_per_trait == qc.min_samples_per_trait == 10


def test_canonical_thresholds_match_upstream_delegate_defaults():
    """Drift tripwire pinning the hardcoded _CANONICAL_* constants to the LIVE upstream
    delegate rather than to each other (the mirror test above only proves lockstep). On the
    pinned analyze version the delegate's own signature defaults coincide with the canonical
    QC-pipeline values; if a future bump changes them this fails, prompting a re-verify against
    the pipeline canonical instead of a silent desync."""
    import inspect

    from bloom_mcp.tools import _qc_shared

    sig = inspect.signature(qc_inspect_tool.apply_data_cleanup_filters).parameters
    assert (
        _qc_shared._CANONICAL_MAX_ZEROS_PER_TRAIT == sig["max_zeros_per_trait"].default
    )
    assert _qc_shared._CANONICAL_MAX_NANS_PER_TRAIT == sig["max_nans_per_trait"].default
    assert (
        _qc_shared._CANONICAL_MAX_NANS_PER_SAMPLE == sig["max_nans_per_sample"].default
    )
    assert (
        _qc_shared._CANONICAL_MIN_SAMPLES_PER_TRAIT
        == sig["min_samples_per_trait"].default
    )


# ── 3.3 provenance + links (not blobs) ──────────────────────────────────────


def test_provenance_stamped_seed_none_and_links_returned(injected_ports):
    _reader, store = injected_ports
    result = _run()

    stored = store.get_run(_EXPERIMENT, "qc_inspect", "latest")
    assert stored.tool == "qc_inspect"
    assert stored.seed is None  # QC inspection is deterministic — no random_state
    # Load-bearing outputs the report always commits (the recommendation + the per-trait
    # overlay + the NaN-samples table). The missingness heatmap is best-effort (it can be
    # absent on a degenerate frame — see _render_report), so it is NOT asserted here.
    load_bearing = {
        "trait_eda_overview.png",
        "variance_distribution.png",
        "nan_samples.csv",
        "recommendation.json",
    }
    assert load_bearing <= set(stored.output_keys)
    # On this (non-degenerate) fixture the heatmap IS produced.
    assert "missing_data_pattern.png" in stored.output_keys

    assert result.run_ref == stored.run_ref
    assert result.manifest_path == stored.manifest_path
    assert load_bearing <= set(result.outputs)

    # bloom#581: a signed link + hash + size per output.
    assert set(result.output_links) == set(result.outputs)
    for name, key in result.outputs.items():
        link = result.output_links[name]
        assert link.key == key
        assert link.url
        assert link.sha256 == stored.output_sha256[name]
        assert link.size_bytes >= 0
    assert stored.output_links == {}

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


def test_reads_raw_even_when_a_cleaned_version_already_exists():
    """qc_inspect exists to pick qc_clean's thresholds BEFORE cleaning — it must
    read the RAW frame even when a cleaned version already exists for the
    experiment, not whatever version="latest" would resolve to (reading an
    already-cleaned frame to choose its own cleaning thresholds is circular).
    A cleaned version with a different row count makes an accidental
    version="latest" read detectable."""
    raw_df = _raw_df()
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, raw_df)
    reader.add_cleaned_version(_EXPERIMENT, "v1", raw_df.iloc[:5], make_latest=True)
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)
    try:
        result = _run()
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert result.source == "raw"
    assert result.n_samples == len(raw_df)


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


# ── recommendation is benefit-aware: no drop advised when it frees no samples ───


def test_no_drop_recommended_when_it_would_save_no_samples():
    """Blocking regression: a kept NaN-bearing trait under a LOOSE max_nans_per_sample
    loses zero samples, so lowering max_nans_per_trait to drop it buys nothing on sample
    loss. The recommendation must say no_change_needed (not advise a 0→0 drop), while still
    reporting the trait's missingness footprint so the agent isn't blind to it."""
    n = 50
    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(n)],
            "geno": ["g1", "g2"] * (n // 2),
            "rep": list(range(n)),
            "t_ok": [float(i + 1) for i in range(n)],
            "t_nan": [float(i + 1) for i in range(n)],
        }
    )
    df.loc[[3, 17], "t_nan"] = float("nan")  # 2/50 = 0.04 NaN, kept at default 0.2

    reader = FakeReader()
    reader.add_experiment("loose.csv", df)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        result = qc_inspect(
            QCInspectParams(
                experiment="loose.csv",
                max_nans_per_sample=0.5,  # tolerates the missingness → 0 samples lost
                trait_columns=["t_ok", "t_nan"],
            )
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert result.samples_lost_at_current_params == 0
    rec = result.recommendation
    assert rec.no_change_needed is True
    assert rec.recommended_max_nans_per_trait is None
    assert rec.would_remove_traits == []
    assert rec.samples_lost_at_recommendation == 0
    # The offending trait's footprint is still surfaced (2 samples carry its NaN).
    assert rec.offending_trait_nan_counts == {"t_nan": 2}


# ── traits_would_be_removed carries a per-trait removal reason (NaN vs zeros/min) ──


def test_removed_trait_reasons_explains_non_nan_removals():
    """A trait removed by the ZERO filter is in traits_would_be_removed but NOT in the
    NaN-only traits_exceeding_thresholds; removed_trait_reasons explains the difference so
    the two fields never look contradictory without cause."""
    n = 50
    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(n)],
            "geno": ["g1", "g2"] * (n // 2),
            "rep": list(range(n)),
            "t_ok": [float(i + 1) for i in range(n)],
            "t_zeros": [0.0] * 30
            + [float(i + 1) for i in range(20)],  # 60% zeros > 0.5
        }
    )
    reader = FakeReader()
    reader.add_experiment("zeros.csv", df)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        result = qc_inspect(
            QCInspectParams(experiment="zeros.csv", trait_columns=["t_ok", "t_zeros"])
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert result.per_trait_nan_fraction["t_zeros"] == 0.0  # no NaN...
    assert "t_zeros" not in result.traits_exceeding_thresholds  # ...so not NaN-flagged
    assert "t_zeros" in result.traits_would_be_removed  # ...but the delegate drops it
    assert result.removed_trait_reasons["t_zeros"] == "too_many_zeros"


# ── detected roles are forwarded (proven with non-default capitalized roles) ────


def test_detected_roles_are_forwarded_overriding_delegate_defaults(monkeypatch):
    """Capitalized Genotype/Replicate differ from the delegate defaults geno/rep, so this
    distinguishes 'forwards detected roles' from 'delegate applied its own defaults' — the
    same guard qc_clean's suite uses (the geno/rep/Barcode spy alone cannot prove it).
    """
    df = pd.DataFrame(
        {
            "Genotype": (["g1", "g2"] * 8),
            "Replicate": list(range(16)),
            "tA": [float(i) for i in range(16)],
            "tB": [float(2 * i) for i in range(16)],
        }
    )
    reader = FakeReader()
    reader.add_experiment("caps.csv", df)
    _ports.configure(reader=reader, store=FakeResultStore())

    captured = {}
    real_filter = qc_inspect_tool.apply_data_cleanup_filters

    def _spy(df_, trait_cols=None, **kwargs):
        captured["kwargs"] = kwargs
        return real_filter(df_, trait_cols, **kwargs)

    monkeypatch.setattr(qc_inspect_tool, "apply_data_cleanup_filters", _spy)
    try:
        qc_inspect(QCInspectParams(experiment="caps.csv"))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert captured["kwargs"]["genotype_col"] == "Genotype"
    assert captured["kwargs"]["replicate_col"] == "Replicate"
    # sample_id undetected here → barcode_col omitted (not forwarded as a wrong default).
    assert captured["kwargs"].get("barcode_col") != "Genotype"


# ── recommendation threshold: exact 0.01-boundary branch ────────────────────────


def test_recommendation_at_exact_hundredth_boundary_steps_down_one():
    """When the smallest offending NaN fraction lands exactly on a 0.01 step (0.15), the
    floor equals the fraction, so the recommendation must step one 0.01 below it (0.14) to
    strictly drop the trait — exercising the round(min_frac - 0.01) branch."""
    n = 20
    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(n)],
            "geno": ["g1", "g2"] * (n // 2),
            "rep": list(range(n)),
            "t_ok": [float(i + 1) for i in range(n)],
            "t_nan": [float(i + 1) for i in range(n)],
        }
    )
    df.loc[[1, 5, 9], "t_nan"] = float(
        "nan"
    )  # 3/20 = 0.15 exactly, kept at default 0.2

    reader = FakeReader()
    reader.add_experiment("boundary.csv", df)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        result = qc_inspect(
            QCInspectParams(experiment="boundary.csv", trait_columns=["t_ok", "t_nan"])
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert result.per_trait_nan_fraction["t_nan"] == 0.15
    rec = result.recommendation
    assert rec.no_change_needed is False
    assert rec.recommended_max_nans_per_trait == 0.14  # one 0.01 step below 0.15
    assert "t_nan" in rec.would_remove_traits


# ── empty trait_columns is a caller mistake, not "inspect everything" ────────────


def test_empty_trait_columns_is_invalid_input(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        qc_inspect(QCInspectParams(experiment=_EXPERIMENT, trait_columns=[]))
    assert exc.value.code == "invalid_input"


def test_metadata_only_frame_with_no_traits_is_invalid_input():
    """An auto-detected empty trait set (no numeric traits) is rejected up front, not
    committed as a useless empty report run."""
    df = pd.DataFrame(
        {"Barcode": ["b0", "b1"], "geno": ["g1", "g2"], "note": ["x", "y"]}
    )
    reader = FakeReader()
    reader.add_experiment("meta_only.csv", df)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        with pytest.raises(BloomMCPError) as exc:
            qc_inspect(QCInspectParams(experiment="meta_only.csv"))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    assert exc.value.code == "invalid_input"


# ── experiment must be a bare filename (path-traversal guard) ────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        "../../app/.env",
        "/etc/passwd",
        "sub/dir/x.csv",
        "..\\..\\app\\.env",
        "sub\\dir\\x.csv",
        "..",
        ".",
        "",
    ],
)
def test_experiment_path_traversal_is_rejected(injected_ports, bad):
    """A non-bare experiment (separators / .. / absolute / empty) is rejected before any
    read, so this read-and-persist tool can't be turned into an arbitrary-file exfil path.

    Includes backslash-separated variants: ``pathlib.Path`` only treats ``\\`` as a
    separator on Windows, so on POSIX a naive ``Path(x).name != x`` check alone lets a
    backslash payload slip through to a "file not found" read attempt instead of being
    rejected here.
    """
    with pytest.raises(BloomMCPError) as exc:
        qc_inspect(QCInspectParams(experiment=bad))
    assert exc.value.code == "invalid_input"


# ── inf / -inf is surfaced, not silently read as 0% missing ─────────────────────


def test_infinite_values_are_flagged_not_counted_as_missing():
    """A trait that is 40% inf reads as ~0% NaN (isna ignores inf) and is kept — the result
    must surface per_trait_inf_count and prepend an inf warning to the recommendation
    rationale, since this is exactly the silent bias the tool exists to catch."""
    n = 20
    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(n)],
            "geno": ["g1", "g2"] * (n // 2),
            "rep": list(range(n)),
            "t_ok": [float(i + 1) for i in range(n)],
            "t_inf": [float(i + 1) for i in range(n)],
        }
    )
    df.loc[list(range(8)), "t_inf"] = float("inf")  # 8/20 = 40% inf, 0% NaN

    reader = FakeReader()
    reader.add_experiment("inf.csv", df)
    _ports.configure(reader=reader, store=FakeResultStore())
    try:
        result = qc_inspect(
            QCInspectParams(experiment="inf.csv", trait_columns=["t_ok", "t_inf"])
        )
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert result.per_trait_nan_fraction["t_inf"] == 0.0  # isna ignores inf
    assert result.per_trait_inf_count == {"t_inf": 8}
    assert "inf" in result.recommendation.rationale.lower()


# ── delegate cleanup-log contract drift surfaces structurally, not as silent 0 ──


def test_cleanup_log_missing_keys_is_structured_internal_error(
    injected_ports, monkeypatch
):
    """If the delegate log ever drops original_samples/final_samples, samples-lost must NOT
    silently become 0 (which would flip every recommendation to no_change_needed) — it
    surfaces as a structured internal_error."""

    def _bad_log(df, trait_cols=None, **kwargs):
        return df[list(trait_cols or [])].copy(), {
            "removed_traits": []
        }  # no sample keys

    monkeypatch.setattr(qc_inspect_tool, "apply_data_cleanup_filters", _bad_log)
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "internal_error"


# ── render/commit failure cleans up its staging dir (no leaked temp run) ────────


def test_render_failure_cleans_staging_and_commits_nothing(injected_ports, monkeypatch):
    """A failure AFTER create_run (inside _render_report) must remove the staging dir and
    commit no run — the existing delegate-raise test injects BEFORE create_run, so this path
    was untested."""
    _reader, store = injected_ports

    captured = {}
    real_create = store.create_run

    def _spy_create(*a, **k):
        run = real_create(*a, **k)
        captured["staging_dir"] = run.staging_dir
        return run

    monkeypatch.setattr(store, "create_run", _spy_create)

    def _boom_eda(*a, **k):
        raise RuntimeError("render failed after the run dir was created")

    monkeypatch.setattr(qc_inspect_tool, "create_trait_eda_plots", _boom_eda)

    with pytest.raises(BloomMCPError):
        _run()
    assert store.list_runs(_EXPERIMENT, "qc_inspect") == []  # nothing committed
    assert not captured["staging_dir"].exists()  # staging dir removed


# ── heatmap is best-effort: run still commits when it can't be produced ─────────


def test_run_commits_without_heatmap_when_summary_plots_fail(
    injected_ports, monkeypatch
):
    """When create_exploratory_summary_plots raises, the report still commits its
    load-bearing outputs, just without missing_data_pattern.png (best-effort heatmap).
    """
    _reader, store = injected_ports

    def _boom_summary(*a, **k):
        raise RuntimeError("degenerate frame: heatmap unavailable")

    monkeypatch.setattr(
        qc_inspect_tool, "create_exploratory_summary_plots", _boom_summary
    )

    result = _run()
    assert "missing_data_pattern.png" not in result.outputs
    assert {"trait_eda_overview.png", "nan_samples.csv", "recommendation.json"} <= set(
        result.outputs
    )
    assert store.get_run(_EXPERIMENT, "qc_inspect", "latest").run_ref == "v1"


# ── cylinder oracle (#483) ───────────────────────────────────────────────────
#
# See tests/fixtures/README.md's "Cross-tier oracle fixtures (cylinder)" section.
# Recorded via a real MCP call at qc_inspect's canonical defaults; since qc_clean's
# own cylinder golden shows zero drops at those thresholds, this result is
# numerically identical to inspecting genuinely raw data here -- no missingness
# tradeoff to demonstrate, unlike turface_19's NaN-heavy-trait recommendation.

_RAW_CYL = _FIXTURES / "cylinder_raw_data.csv"
_GOLDEN_CYL = json.loads(
    (_FIXTURES / "cylinder_qc_inspect_golden.json").read_text(encoding="utf-8")
)
_EXPERIMENT_CYL = "cylinder_raw.csv"


@pytest.fixture
def injected_ports_cylinder():
    reader = FakeReader()
    store = FakeResultStore()
    reader.add_experiment(_EXPERIMENT_CYL, pd.read_csv(_RAW_CYL, encoding="utf-8"))
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_cylinder_inspect_matches_golden_no_missingness_story(injected_ports_cylinder):
    result = qc_inspect(QCInspectParams(experiment=_EXPERIMENT_CYL))

    assert result.n_samples == _GOLDEN_CYL["raw_samples"] == 129
    assert result.n_traits == _GOLDEN_CYL["raw_traits"] == 846
    assert (
        result.traits_would_be_removed
        == _GOLDEN_CYL["at_default_params"]["traits_would_be_removed"]
        == []
    )
    assert (
        result.samples_lost_at_current_params
        == _GOLDEN_CYL["at_default_params"]["samples_lost"]
        == 0
    )
    assert (
        result.recommendation.no_change_needed
        == _GOLDEN_CYL["recommendation"]["no_change_needed"]
        is True
    )


# ── BLOOM_LOCAL_ROOT-only mode (#479 regression) ─────────────────────────────
#
# qc_inspect self-computes its report run's source_csv rather than calling
# _ports.start_run; it must route that through the active reader
# (_ports.raw_source_for), not a bare BLOOM_TRAITS_DIR read — the latter
# resolves to Path("") (CWD) when BLOOM_TRAITS_DIR is unset, which is now a
# supported combination when BLOOM_STORAGE_BACKEND=local + BLOOM_LOCAL_ROOT.


def test_source_csv_honors_local_root_only_mode(tmp_path, monkeypatch):
    import bloom_mcp.experiment_utils as eu
    import bloom_mcp.storage_backend as sb
    from bloom_mcp.data_access import LocalReader

    root = tmp_path / "local_root"
    (root / "input").mkdir(parents=True)
    raw_path = root / "input" / _EXPERIMENT
    _raw_df().to_csv(raw_path, index=False)

    monkeypatch.delenv("BLOOM_TRAITS_DIR", raising=False)
    monkeypatch.delenv("BLOOM_EXPERIMENT_LOCAL_ROOT", raising=False)
    monkeypatch.setattr(eu, "TRAITS_DIR", Path("/should-not-be-used"))
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_LOCAL_ROOT", str(root))
    sb.reset_backend_for_tests()

    store = FakeResultStore()
    captured = {}
    real_create_run = store.create_run

    def _spy(**kwargs):
        captured.update(kwargs)
        return real_create_run(**kwargs)

    monkeypatch.setattr(store, "create_run", _spy)
    _ports.configure(reader=LocalReader(), store=store)
    try:
        _run()
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert captured["source_csv"] == raw_path
    assert captured["source_csv"].exists()


# ── explicit source pin (#626) ──────────────────────────────────────────────
# The multi-source test double (FakeReader + a bolted-on SourceSelectable
# surface) lives in the root tests/conftest.py as make_multi_source_fake_reader
# — it was duplicated near-verbatim across this file, test_qc_clean_tool.py,
# and test_ports.py before being consolidated there.


@pytest.fixture
def multi_source_ports(make_multi_source_fake_reader):
    reader = make_multi_source_fake_reader([9, 10])
    store = FakeResultStore()
    reader.add_experiment(_EXPERIMENT, _raw_df())
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_source_id_and_run_id_fields_exist():
    assert "source_id" in QCInspectParams.model_fields
    assert "run_id" in QCInspectParams.model_fields


def test_omitting_both_source_params_preserves_todays_behavior(injected_ports):
    """No behavior change beyond accepting (and ignoring, when None) the two
    new fields — same recommendation oracle as before this change, plus a
    source_note that must stay None on a single-source (FakeReader) experiment."""
    result = _run()
    assert result.n_samples == 187
    assert result.source_note is None


def test_explicit_source_pin_changes_which_source_is_inspected(multi_source_ports):
    _reader, _store = multi_source_ports
    result_9 = _run(source_id=9)
    result_10 = _run(source_id=10)
    # Both resolve (no error) — proving the pin actually reached load_experiment
    # rather than being silently dropped.
    assert result_9.n_samples > 0
    assert result_10.n_samples > 0
    # A pin was given, so there is nothing to advise.
    assert result_9.source_note is None
    assert result_10.source_note is None


def test_multi_source_experiment_with_no_pin_gets_an_advisory_note(multi_source_ports):
    result = _run()
    assert result.source_note is not None
    assert "2 sources" in result.source_note
    assert "core_list_experiment_sources" in result.source_note
    assert "10" in result.source_note  # the resolved (max) source_id


def test_both_source_id_and_run_id_given_is_rejected(multi_source_ports):
    with pytest.raises(BloomMCPError) as exc:
        _run(source_id=9, run_id="p10")
    assert (
        "source_id" in exc.value.message.lower()
        or "run_id" in exc.value.message.lower()
    )


def test_source_pin_matching_nothing_is_rejected(multi_source_ports):
    with pytest.raises(BloomMCPError):
        _run(source_id=404)


def test_source_pinning_unsupported_on_fakereader_surfaces_as_bloommcperror(
    injected_ports,
):
    with pytest.raises(BloomMCPError):
        _run(source_id=7)
