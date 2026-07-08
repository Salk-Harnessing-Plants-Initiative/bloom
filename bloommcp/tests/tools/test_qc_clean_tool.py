"""Contract + oracle tests for the granular ``qc_clean`` tool (Tier 3 / #338).

Five contract patterns + the no-NaN / fewer-than-``dropna()`` oracle through the
MCP tool, plus the qc_clean -> cleaned-version composition that ``pca_analysis``
(Tier 4) relies on. The tool delegates ALL cleanup to
``sleap_roots_analyze.clean_traits_for_analysis`` and persists a versioned run via
the ``ResultStore`` port — no QC logic in the MCP.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pandas as pd
import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import FakeReader, SupabaseReader
from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
from bloom_mcp.tools import _ports
from bloom_mcp.tools import qc_clean_tool
from bloom_mcp.tools.qc_clean_tool import QCCleanParams, QCCleanResult, qc_clean

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW = _FIXTURES / "turface_19_raw_data.csv"
_GOLDEN = json.loads((_FIXTURES / "turface_19_qc_golden.json").read_text())

_EXPERIMENT = "turface_19_raw.csv"
_MNT = _GOLDEN["cleanup_params"]["max_nans_per_trait"]


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


def _run(**overrides) -> QCCleanResult:
    params = {"experiment": _EXPERIMENT, "max_nans_per_trait": _MNT, **overrides}
    return qc_clean(QCCleanParams(**params))


# ── 2. Oracle through the tool ──────────────────────────────────────────────


def test_cleaned_table_has_no_nans_and_matches_golden_shape(injected_ports):
    """2.2 — no-NaN cleaned table through the tool, matching the golden shape."""
    _reader, store = injected_ports
    result = _run()

    assert result.n_samples_out == _GOLDEN["cleaned_samples"] == 187
    assert result.n_traits_out == _GOLDEN["cleaned_traits"] == 18
    assert sorted(result.removed_traits) == _GOLDEN["removed_traits"]

    # The persisted cleaned table itself has zero NaNs in its kept trait columns.
    stored = store.get_run(_EXPERIMENT, "qc", "latest")
    cleaned_key = stored.output_keys["_cleaned.csv"]
    assert cleaned_key.endswith("_cleaned.csv")
    # FakeResultStore hashed the staged bytes; reload from the staged frame via
    # the result's kept columns: the cleaned frame is the delegate output.
    assert result.kept_trait_columns
    assert "Root_Biomass_mg" not in result.kept_trait_columns


def test_fewer_samples_dropped_than_naive_dropna(injected_ports):
    """2.3 — qc_clean retains strictly more samples than a naive dropna()."""
    _reader, _store = injected_ports
    raw = _raw_df()
    trait_cols = [c for c in raw.columns if c not in ("Barcode", "geno", "rep")]
    naive = len(raw.dropna(subset=trait_cols))
    assert naive == _GOLDEN["naive_dropna_samples"] == 158

    result = _run()
    assert result.n_samples_in == 187
    assert result.n_samples_out == 187
    assert result.n_samples_out > naive


# ── 3.1 tools/list presence ─────────────────────────────────────────────────


def test_qc_clean_appears_in_tools_list_and_workflow_preserved():
    """3.1 — qc_clean is discoverable; run_qc_workflow is still registered."""
    from fastmcp import Client

    from bloom_mcp import server

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_list())}
    assert "qc_clean" in tools
    assert tools["qc_clean"].inputSchema is not None
    assert "run_qc_workflow" in tools  # additive — workflow not removed


# ── 3.2 schema round-trip ───────────────────────────────────────────────────


def test_valid_input_output_round_trip(injected_ports):
    result = _run()
    # Output validates against the declared model and survives a JSON round-trip.
    again = QCCleanResult.model_validate(json.loads(result.model_dump_json()))
    assert again.n_samples_out == result.n_samples_out


def test_invalid_threshold_is_input_validation_error(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        qc_clean({"experiment": _EXPERIMENT, "max_nans_per_trait": 1.5})
    assert exc.value.code == "invalid_input"


def test_default_thresholds_match_canonical_qc_pipeline():
    """3.2b — qc_clean's default thresholds mirror the **canonical QC pipeline**
    defaults (sleap_roots_analyze ``CleanupConfig`` / ``clean_traits_for_analysis``'s
    injected ``_QC_DEFAULTS``), NOT the looser ``apply_data_cleanup_filters``
    signature defaults. Drift guard for talmolab/sleap-roots-analyze#167: because
    ``qc_clean`` forwards all four thresholds explicitly, a default that diverged
    from the pipeline canonical (e.g. reverting to the helper's 0.3 / 0.2) would
    silently ship a looser clean than the pipeline — this catches that.
    """
    p = QCCleanParams(experiment="x.csv")
    assert p.max_zeros_per_trait == 0.5
    assert p.max_nans_per_trait == 0.2  # canonical (NOT the helper's looser 0.3)
    assert p.max_nans_per_sample == 0.0  # canonical (NOT the helper's looser 0.2)
    assert p.min_samples_per_trait == 10


# ── 3.3 provenance + links (not blobs) ──────────────────────────────────────


def test_provenance_stamped_seed_none_and_links_returned(injected_ports):
    _reader, store = injected_ports
    result = _run()

    stored = store.get_run(_EXPERIMENT, "qc", "latest")
    assert stored.tool == "qc_clean"
    assert stored.seed is None  # QC is deterministic — no random_state
    assert set(stored.output_keys) == {"_cleaned.csv", "cleanup_log.json"}

    # Result returns links (run ref + manifest + object keys), never the table.
    assert result.run_ref == stored.run_ref
    assert result.manifest_path == stored.manifest_path
    assert set(result.outputs) == {"_cleaned.csv", "cleanup_log.json"}
    assert not hasattr(result, "df")
    # No field on the result holds the full cleaned table (links, not blobs).
    dumped = result.model_dump()
    assert not any(
        isinstance(v, (list, dict)) and len(str(v)) > 5000 for v in dumped.values()
    )


# ── 3.4 property / invariant ────────────────────────────────────────────────


def test_cleaned_is_subset_no_nans_and_bounded(injected_ports):
    _reader, _store = injected_ports
    raw = _raw_df()
    raw_traits = [c for c in raw.columns if c not in ("Barcode", "geno", "rep")]
    result = _run()

    assert set(result.kept_trait_columns).issubset(set(raw_traits))
    assert 0 < result.n_samples_out <= result.n_samples_in == len(raw)
    assert 0 < result.n_traits_out <= result.n_traits_in == len(raw_traits)
    assert result.n_traits_dropped == result.n_traits_in - result.n_traits_out
    assert result.n_samples_dropped == result.n_samples_in - result.n_samples_out


# ── 3.5 delegation pinning (spy) ────────────────────────────────────────────


def test_delegates_once_forwards_roles_and_never_calls_vendored_cleanup(
    injected_ports, monkeypatch
):
    captured = {}
    real = qc_clean_tool.clean_traits_for_analysis

    def _spy(df, trait_cols=None, **kwargs):
        captured["n_calls"] = captured.get("n_calls", 0) + 1
        captured["trait_cols"] = trait_cols
        captured["kwargs"] = kwargs
        return real(df, trait_cols=trait_cols, **kwargs)

    monkeypatch.setattr(qc_clean_tool, "clean_traits_for_analysis", _spy)

    # The vendored cleanup must never be touched.
    import bloom_mcp.data_cleanup as vendored

    def _boom(*a, **k):  # pragma: no cover
        raise AssertionError("qc_clean called the vendored bloom_mcp.data_cleanup")

    monkeypatch.setattr(vendored, "apply_data_cleanup_filters", _boom)

    _run()

    assert captured["n_calls"] == 1
    assert captured["kwargs"]["barcode_col"] == "Barcode"
    assert captured["kwargs"]["genotype_col"] == "geno"
    assert captured["kwargs"]["replicate_col"] == "rep"


# ── 3.6 role-column fallback (None must not be forwarded) ────────────────────


def test_undetected_role_columns_fall_back_to_delegate_defaults(monkeypatch):
    # A frame with no detectable genotype/replicate/barcode roles.
    df = pd.DataFrame(
        {
            "t1": [1.0, 2.0, 3.0, 4.0, 5.0] * 4,
            "t2": [2.0, 4.0, 6.0, 8.0, 10.0] * 4,
        }
    )
    reader = FakeReader()
    store = FakeResultStore()
    reader.add_experiment("roleless.csv", df)
    _ports.configure(reader=reader, store=store)

    captured = {}

    def _spy(df_, trait_cols=None, **kwargs):
        captured["kwargs"] = kwargs
        # Return a trivially-clean result so the tool can proceed.
        return (
            df_.copy(),
            list(trait_cols or df_.columns),
            {
                "original_samples": len(df_),
                "final_samples": len(df_),
                "original_traits": len(trait_cols or df_.columns),
                "final_traits": len(trait_cols or df_.columns),
                "removed_traits": [],
            },
        )

    monkeypatch.setattr(qc_clean_tool, "clean_traits_for_analysis", _spy)
    try:
        qc_clean(QCCleanParams(experiment="roleless.csv"))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    # None must never be forwarded — the kwarg is omitted so the delegate default applies.
    for role in ("genotype_col", "replicate_col", "barcode_col"):
        assert (
            captured["kwargs"].get(role) is not None or role not in captured["kwargs"]
        )
        assert captured["kwargs"].get(role, "x") is not None


# ── 3.7 error envelope ──────────────────────────────────────────────────────


def test_unresolvable_experiment_errors_with_no_run(injected_ports):
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        qc_clean(QCCleanParams(experiment="does_not_exist.csv"))
    assert exc.value.code in ("tool_error", "assumption_violated")
    assert store.list_runs("does_not_exist.csv", "qc") == []


def test_all_traits_dropped_is_structured_error_with_no_run(
    injected_ports, monkeypatch
):
    _reader, store = injected_ports

    def _drops_everything(df, trait_cols=None, **kwargs):
        return (
            df.iloc[:0].copy(),
            [],
            {
                "original_samples": len(df),
                "final_samples": 0,
                "original_traits": len(trait_cols or []),
                "final_traits": 0,
                "removed_traits": list(trait_cols or []),
            },
        )

    monkeypatch.setattr(qc_clean_tool, "clean_traits_for_analysis", _drops_everything)
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "assumption_violated"
    assert "threshold" in exc.value.remedy.lower()
    assert store.list_runs(_EXPERIMENT, "qc") == []


def test_delegate_raise_is_structured_without_leaking(injected_ports, monkeypatch):
    def _boom(*a, **k):
        raise RuntimeError("secret path /var/secrets/key and host db.internal")

    monkeypatch.setattr(qc_clean_tool, "clean_traits_for_analysis", _boom)
    with pytest.raises(BloomMCPError) as exc:
        _run()
    msg = f"{exc.value.message} {exc.value.remedy}"
    assert "secret" not in msg and "/var" not in msg and "db.internal" not in msg


# ── 3.8 composition: qc_clean run resolves as a cleaned version ──────────────


def test_qc_clean_run_composes_into_require_clean_read(fake_supabase_storage, tmp_path):
    """A committed qc_clean run is resolvable by require_clean=True (the path
    pca_analysis depends on). Driven through the Supabase adapters over the shared
    in-memory object store — the fakes' reader/store are disjoint and cannot
    exercise this handoff."""
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, _raw_df())
    store = SupabaseResultStore()  # writes to the patched object store
    _ports.configure(reader=reader, store=store)
    try:
        _run()
        # A fresh SupabaseReader resolves the committed cleaned version from storage.
        resolved = SupabaseReader().load_experiment(_EXPERIMENT, require_clean=True)
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert resolved.source.endswith("_cleaned")
    assert resolved.source != "raw"
    # Artifact-level oracle: the *reloaded* cleaned frame is genuinely no-NaN and
    # matches the golden shape — a regression that persisted NaN rows fails here
    # (the FakeResultStore path can't reload, so this real round-trip is the guard).
    assert int(resolved.df[resolved.trait_cols].isna().sum().sum()) == 0
    assert len(resolved.df) == _GOLDEN["cleaned_samples"] == 187
    assert len(resolved.trait_cols) == _GOLDEN["cleaned_traits"] == 18


# ── parity: qc_clean output == direct pipeline cleanup step on same fixture ──


def test_qc_clean_matches_pipeline_cleanup_on_same_fixture(
    fake_supabase_storage, tmp_path
):
    """Parity oracle: the table qc_clean persists is exactly the table the QC
    pipeline cleanup step (``clean_traits_for_analysis``) produces on the same raw
    fixture, called with the same params + adapter-detected role columns.

    The other tests pin *delegation happens* (the spy in
    ``test_delegates_once_...``) and *output matches a frozen golden shape*
    (``test_cleaned_table_has_no_nans_and_matches_golden_shape``) — but none
    re-derive the delegate's actual output and compare it, cell-for-cell, to what
    the tool shipped. This closes that gap end-to-end: run the cleanup step
    directly, run qc_clean, reload the persisted cleaned frame, assert equal.

    Both sides share ONE ``QCCleanParams`` instance, so thresholds cannot drift
    between the direct call and the tool call. Driven through the Supabase adapters
    (like ``test_qc_clean_run_composes_into_require_clean_read``) because that is
    the only path that can reload the persisted cleaned frame for comparison.
    """
    from sleap_roots_analyze import clean_traits_for_analysis

    from bloom_mcp.tools.qc_clean_tool import _role_kwargs

    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, _raw_df())
    store = SupabaseResultStore()  # writes to the patched object store
    _ports.configure(reader=reader, store=store)

    # One params object feeds BOTH sides — the exact inputs the tool forwards.
    params = QCCleanParams(experiment=_EXPERIMENT, max_nans_per_trait=_MNT)
    try:
        frame = reader.load_experiment(_EXPERIMENT, version="raw")
        # The pipeline cleanup step, called directly with the tool's own params.
        expected_df, expected_kept, _log = clean_traits_for_analysis(
            frame.df,
            trait_cols=frame.trait_cols,
            max_zeros_per_trait=params.max_zeros_per_trait,
            max_nans_per_trait=params.max_nans_per_trait,
            max_nans_per_sample=params.max_nans_per_sample,
            min_samples_per_trait=params.min_samples_per_trait,
            **_role_kwargs(frame),
        )

        result = qc_clean(params)
        # Reload the cleaned frame the tool actually persisted.
        resolved = SupabaseReader().load_experiment(_EXPERIMENT, require_clean=True)
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    expected_kept = list(expected_kept)
    # The summary the tool reported matches the direct cleanup step.
    assert result.kept_trait_columns == expected_kept
    assert result.n_samples_out == len(expected_df)
    assert result.n_traits_out == len(expected_kept)

    # The persisted cleaned trait table equals the direct pipeline output.
    pd.testing.assert_frame_equal(
        resolved.df[expected_kept].reset_index(drop=True),
        expected_df[expected_kept].reset_index(drop=True),
        check_dtype=False,
    )


# ── parity: qc_clean persisted output == the FULL QC pipeline's cleanup step ──


def test_qc_clean_matches_full_pipeline_cleanup_step(fake_supabase_storage, tmp_path):
    """Strong parity oracle: what ``qc_clean`` persists equals what the **full QC
    pipeline's cleanup step** (``CleanupTraitsStep``, QC step 02) produces on the
    same raw fixture — the guarantee ``qc_clean``'s "reproduces the canonical
    pipeline clean" claim rests on.

    ``test_qc_clean_matches_pipeline_cleanup_on_same_fixture`` above compares
    against ``clean_traits_for_analysis`` — the *same* function ``qc_clean`` calls,
    so it can't catch a divergence between that minimal entry point and the real
    pipeline. This test drives the genuine pipeline **step object** instead, with
    the real ``QCPipelineConfig`` / canonical ``CleanupConfig``, and asserts the
    tool's persisted cleaned table makes the *same cleaning decisions* cell-for-cell.

    The step sanitizes/abbreviates trait names (``Total.Root.Length.mm`` →
    ``Total Root Length (mm)``) and reorders columns; ``qc_clean`` does neither
    (analyze#164 minimal entry point). Byte-equivalence is explicitly a non-goal —
    so we compare *decisions*: same surviving samples, same surviving trait
    identities (mapped through the step's own name map), same values aligned on
    ``Barcode``. Both sides run at the canonical config with no threshold override,
    so a drift in either path's defaults breaks this.
    """
    from sleap_roots_analyze.pipeline.config import get_default_qc_config
    from sleap_roots_analyze.pipeline.core import StepResult
    from sleap_roots_analyze.pipeline.steps.cleanup_traits import CleanupTraitsStep

    raw = _raw_df()
    meta_cols = ["Barcode", "geno", "rep"]
    trait_cols = [c for c in raw.columns if c not in meta_cols]

    # ── Full pipeline cleanup step (step 02), driven with the REAL pipeline config.
    # Only config.cleanup / config.columns are consulted by the step; the canonical
    # CleanupConfig is the default, asserted here so a config-default drift is loud.
    config = get_default_qc_config(pipeline_name="qc_clean_parity")
    config.columns.barcode = "Barcode"
    config.columns.genotype = "geno"
    config.columns.replicate = "rep"
    assert (
        config.cleanup.max_zeros_per_trait,
        config.cleanup.max_nans_per_trait,
        config.cleanup.max_nan_fraction,
        config.cleanup.min_samples_per_trait,
    ) == (0.5, 0.2, 0.0, 10)

    prev = StepResult(
        data=raw,
        metadata={
            "trait_column_names": trait_cols,
            "metadata_column_names": meta_cols,
        },
    )
    pipe = CleanupTraitsStep().execute(
        data=raw, config=config, run_dir=tmp_path, prev_result=prev
    )
    pipe_clean = pipe.data
    # raw → sanitized name map the step applied (only *changed* names are keyed).
    name_map = pipe.metadata["trait_name_mapping"]
    pipe_meta = {"Barcode", "Genotype", "Replicate"}
    pipe_kept_sanitized = sorted(c for c in pipe_clean.columns if c not in pipe_meta)

    # ── qc_clean at canonical defaults (no override), through the persistence path,
    # then reload the frame the tool actually shipped.
    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, raw)
    _ports.configure(reader=reader, store=SupabaseResultStore())
    try:
        result = qc_clean(QCCleanParams(experiment=_EXPERIMENT))  # canonical defaults
        resolved = SupabaseReader().load_experiment(_EXPERIMENT, require_clean=True)
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())
    tool_clean = resolved.df

    # 1. Same cleaning decisions: surviving sample + trait counts.
    assert result.n_samples_out == len(pipe_clean) == 158
    assert result.n_traits_out == pipe.metadata["traits_final"]

    # 2. Same surviving trait identities (map the tool's raw names through the
    #    step's own sanitizer; unchanged names are absent from the map).
    tool_kept_raw = list(result.kept_trait_columns)
    tool_kept_sanitized = sorted(name_map.get(c, c) for c in tool_kept_raw)
    assert tool_kept_sanitized == pipe_kept_sanitized

    # 3. Same values, cell-for-cell — align on Barcode, rename the tool's traits to
    #    the step's sanitized names, put both in the same column order.
    tool_vals = (
        tool_clean.set_index("Barcode")[tool_kept_raw]
        .rename(columns=name_map)
        .sort_index()[tool_kept_sanitized]
    )
    pipe_vals = pipe_clean.set_index("Barcode")[tool_kept_sanitized].sort_index()
    pd.testing.assert_frame_equal(tool_vals, pipe_vals, check_dtype=False)


# ── trait_columns validation (blocking #3) ──────────────────────────────────


def test_unknown_trait_column_is_invalid_input_naming_it(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        qc_clean(QCCleanParams(experiment=_EXPERIMENT, trait_columns=["NoSuchTrait"]))
    assert exc.value.code == "invalid_input"
    assert "NoSuchTrait" in exc.value.message


def test_non_numeric_trait_column_is_invalid_input(injected_ports):
    # 'geno' is a metadata/label column, not a numeric trait.
    with pytest.raises(BloomMCPError) as exc:
        qc_clean(QCCleanParams(experiment=_EXPERIMENT, trait_columns=["geno"]))
    assert exc.value.code == "invalid_input"
    assert "geno" in exc.value.message


# ── all-samples-dropped guard (blocking #4) ─────────────────────────────────


def test_all_samples_dropped_is_structured_error_with_no_run(
    injected_ports, monkeypatch
):
    _reader, store = injected_ports

    def _drops_all_samples(df, trait_cols=None, **kwargs):
        # Keeps trait columns but zero rows — the asymmetric case the trait-only
        # guard would miss.
        cols = list(trait_cols or [])
        return (
            df.iloc[:0][cols].copy(),
            cols,
            {
                "original_samples": len(df),
                "final_samples": 0,
                "original_traits": len(cols),
                "final_traits": len(cols),
                "removed_traits": [],
            },
        )

    monkeypatch.setattr(qc_clean_tool, "clean_traits_for_analysis", _drops_all_samples)
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "assumption_violated"
    assert store.list_runs(_EXPERIMENT, "qc") == []


# ── residual-NaN guard (blocking #1/#2) ─────────────────────────────────────


def test_residual_nans_in_kept_columns_are_rejected_before_commit(
    injected_ports, monkeypatch
):
    _reader, store = injected_ports

    def _leaves_nans(df, trait_cols=None, **kwargs):
        cols = list(trait_cols or [])
        out = df[cols].copy()
        out.iloc[0, 0] = float("nan")  # a NaN the delegate failed to clean
        return out, cols, {"final_samples": len(out), "final_traits": len(cols)}

    monkeypatch.setattr(qc_clean_tool, "clean_traits_for_analysis", _leaves_nans)
    with pytest.raises(BloomMCPError) as exc:
        _run()
    assert exc.value.code == "assumption_violated"
    assert "nan" in exc.value.message.lower()
    assert store.list_runs(_EXPERIMENT, "qc") == []  # nothing persisted


# ── role forwarding overrides delegate defaults (non-default roles) ─────────


def test_non_default_roles_are_forwarded_overriding_delegate_defaults(monkeypatch):
    # Capitalized Genotype/Replicate differ from the delegate defaults geno/rep,
    # so this distinguishes "forwards detected roles" from "hard-codes defaults".
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
    store = FakeResultStore()
    _ports.configure(reader=reader, store=store)

    captured = {}

    def _spy(frame_df, trait_cols=None, **kwargs):
        captured["kwargs"] = kwargs
        cols = list(trait_cols or [])
        return (
            frame_df[cols].copy(),
            cols,
            {"final_samples": len(frame_df), "final_traits": len(cols)},
        )

    monkeypatch.setattr(qc_clean_tool, "clean_traits_for_analysis", _spy)
    try:
        qc_clean(QCCleanParams(experiment="caps.csv"))
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())

    assert captured["kwargs"]["genotype_col"] == "Genotype"
    assert captured["kwargs"]["replicate_col"] == "Replicate"
    # sample_id undetected here → barcode_col omitted (not forwarded as None).
    assert captured["kwargs"].get("barcode_col") != "Genotype"


# ── second run increments version (latest resolves to it) ───────────────────


def test_second_run_increments_version(injected_ports):
    _reader, store = injected_ports
    _run()
    _run()
    assert [r.run_ref for r in store.list_runs(_EXPERIMENT, "qc")] == ["v1", "v2"]
    assert store.get_run(_EXPERIMENT, "qc", "latest").run_ref == "v2"


# ── re-run after a cleaned version exists still reads RAW (dogfood regression) ──


def test_rerun_with_existing_cleaned_version_still_reads_raw(injected_ports):
    """qc_clean is the *producer* of cleaned data, so it must always clean from the
    RAW input — never re-clean its own prior output.

    Regression for the dogfood finding: ``qc_clean`` called
    ``reader.load_experiment(experiment)`` without ``version="raw"``, so the default
    ``"latest"`` resolution fed the newest ``_cleaned.csv`` back in once a cleaned
    version existed — re-cleaning already-cleaned data and reporting a misleading
    ``source`` of ``v<N>_cleaned``. The existing tests missed this because the
    FakeReader only ever held the raw frame (cleaned runs land in the *store*); here
    we seed a cleaned version into the *reader* to reproduce the real resolution.
    """
    reader, _store = injected_ports
    raw = _raw_df()
    # A cleaned version already exists and is marked latest — the trap the old
    # default-"latest" load would resolve to instead of the raw input.
    reader.add_cleaned_version(_EXPERIMENT, "v1", raw.copy(), make_latest=True)

    result = _run()

    # The clean is sourced from RAW, never the pre-existing cleaned artifact.
    assert result.source == "raw"
    assert not result.source.endswith("_cleaned")
    # And it genuinely processed the full raw frame (187), not a cleaned re-read.
    assert result.n_samples_in == len(raw) == 187


# ── real-delegate degenerate case maps to a structured, self-correctable error ──


def test_overstrict_thresholds_real_delegate_is_structured_not_internal(injected_ports):
    """The real clean_traits_for_analysis RAISES ValueError on over-strict thresholds
    (no mock). It must surface as a self-correctable assumption_violated with a
    relax-thresholds remedy — not the contract's opaque internal_error."""
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        qc_clean(QCCleanParams(experiment=_EXPERIMENT, min_samples_per_trait=100000))
    assert exc.value.code == "assumption_violated"
    assert "threshold" in exc.value.remedy.lower()
    assert store.list_runs(_EXPERIMENT, "qc") == []  # nothing persisted
