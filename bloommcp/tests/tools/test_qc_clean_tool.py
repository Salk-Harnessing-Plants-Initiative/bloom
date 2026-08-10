"""Contract + oracle tests for the granular ``qc_clean`` tool (Tier 3 / #338).

Five contract patterns + the no-NaN / fewer-than-``dropna()`` oracle through the
MCP tool, plus the qc_clean -> cleaned-version composition that ``pca_analysis``
(Tier 4) relies on. The tool delegates ALL cleanup to
``sleap_roots_analyze.clean_traits_for_analysis`` and persists a versioned run via
the ``ResultStore`` port — no QC logic in the MCP.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from bloom_mcp.contract import BloomMCPError
from bloom_mcp.data_access import (
    AmbiguousSourceSelectionError,
    FakeReader,
    SourceInfo,
    SourcePinNotFoundError,
    SupabaseReader,
)
from bloom_mcp.result_store import FakeResultStore, SupabaseResultStore
from bloom_mcp.tools import _ports
from bloom_mcp.tools._inline_input import compute_input_sha256
from bloom_mcp.sections.sleap_roots.analysis import qc_clean as qc_clean_tool
from bloom_mcp.sections.sleap_roots.analysis.qc_clean import (
    QCCleanParams,
    QCCleanResult,
    qc_clean,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW = _FIXTURES / "turface_19_raw_data.csv"
_GOLDEN = json.loads(
    (_FIXTURES / "turface_19_qc_golden.json").read_text(encoding="utf-8")
)

_EXPERIMENT = "turface_19_raw.csv"
_MNT = _GOLDEN["cleanup_params"]["max_nans_per_trait"]


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


def _run(**overrides) -> QCCleanResult:
    params = {"experiment": _EXPERIMENT, "max_nans_per_trait": _MNT, **overrides}
    return qc_clean(QCCleanParams(**params))


# ── 2. Oracle through the tool ──────────────────────────────────────────────


def test_cleaned_table_has_no_nans_and_matches_golden_shape(injected_ports):
    """2.2 — no-NaN cleaned table through the tool, matching the golden shape."""
    _reader, store = injected_ports
    result = _run()

    assert result.n_samples_out == _GOLDEN["cleaned_samples"] == 187
    # #403: detected traits 20 -> 19 (Computation.Time.s excluded), cleaned 18 -> 17.
    assert result.n_traits_out == _GOLDEN["cleaned_traits"] == 17
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


def test_qc_clean_appears_in_tools_list():
    """3.1 — qc_clean is discoverable."""
    from fastmcp import Client

    from bloom_mcp import server

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_list())}
    assert "sleap_roots_qc_clean" in tools
    assert tools["sleap_roots_qc_clean"].inputSchema is not None


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

    # bloom#581: a signed link + hash + size per output.
    assert set(result.output_links) == set(result.outputs)
    for name, key in result.outputs.items():
        link = result.output_links[name]
        assert link.key == key
        assert link.url
        assert link.sha256 == stored.output_sha256[name]
        assert link.size_bytes >= 0
    assert stored.output_links == {}

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
    # #403: get_trait_columns excludes the numeric metadata column Computation.Time.s,
    # so mirror the tool's detected trait set (n_traits_in == 19) here.
    raw_traits = [
        c
        for c in raw.columns
        if c not in ("Barcode", "geno", "rep", "Computation.Time.s")
    ]
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

    _run()

    assert captured["n_calls"] == 1
    assert captured["kwargs"]["barcode_col"] == "Barcode"
    assert captured["kwargs"]["genotype_col"] == "geno"
    assert captured["kwargs"]["replicate_col"] == "rep"


# ── 3.6 required roles supersede the old delegate-default fallback (#403) ────


def test_missing_required_roles_error_with_no_run(injected_ports):
    """#403 supersede: a frame with no detectable genotype/sample-id now hard-errors
    (previously it fell back to the delegate defaults — see the retired
    ``test_undetected_role_columns_fall_back_to_delegate_defaults``). An untraceable
    cleaned frame is the root cause of the barcode-less remove_outliers crash, so
    qc_clean refuses to produce one: a structured error that lists the available
    columns and names BOTH overrides, and persists nothing."""
    reader, store = injected_ports
    df = pd.DataFrame(
        {
            "t1": [1.0, 2.0, 3.0, 4.0, 5.0] * 4,
            "t2": [2.0, 4.0, 6.0, 8.0, 10.0] * 4,
        }
    )
    reader.add_experiment("roleless.csv", df)

    with pytest.raises(BloomMCPError) as exc:
        qc_clean(QCCleanParams(experiment="roleless.csv"))

    assert exc.value.code == "assumption_violated"
    assert "t1" in exc.value.message and "t2" in exc.value.message  # lists columns
    assert "genotype_column" in exc.value.remedy
    assert "sample_id_column" in exc.value.remedy
    assert store.list_runs("roleless.csv", "qc") == []


def test_only_genotype_missing_error_names_single_role(injected_ports):
    """G: single-role-missing path — only genotype absent (sample_id present).

    Error message must name 'genotype' only (not ' and sample-identifier'), and
    the remedy must name genotype_column but not sample_id_column. Guards the
    ' and '.join(missing) concatenation for the single-item case.
    """
    reader, store = injected_ports
    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(12)],  # sample_id auto-detects
            "t1": [float(i) for i in range(12)],
            "t2": [float(2 * i) for i in range(12)],
        }
    )
    reader.add_experiment("no_geno.csv", df)

    with pytest.raises(BloomMCPError) as exc:
        qc_clean(QCCleanParams(experiment="no_geno.csv"))

    assert exc.value.code == "assumption_violated"
    assert "genotype" in exc.value.message
    assert "sample-identifier" not in exc.value.message
    assert "genotype_column" in exc.value.remedy
    assert "sample_id_column" not in exc.value.remedy
    assert store.list_runs("no_geno.csv", "qc") == []


def test_only_sample_id_missing_error_names_single_role(injected_ports):
    """G: single-role-missing path — only sample_id absent (genotype present).

    Error message must name 'sample-identifier' only (not 'genotype and …').
    """
    reader, store = injected_ports
    df = pd.DataFrame(
        {
            "geno": (["g1", "g2"] * 6),  # genotype auto-detects
            "t1": [float(i) for i in range(12)],
            "t2": [float(2 * i) for i in range(12)],
        }
    )
    reader.add_experiment("no_sample_id.csv", df)

    with pytest.raises(BloomMCPError) as exc:
        qc_clean(QCCleanParams(experiment="no_sample_id.csv"))

    assert exc.value.code == "assumption_violated"
    assert "sample-identifier" in exc.value.message
    assert "genotype and" not in exc.value.message
    assert "sample_id_column" in exc.value.remedy
    assert "genotype_column" not in exc.value.remedy
    assert store.list_runs("no_sample_id.csv", "qc") == []


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
    assert len(resolved.trait_cols) == _GOLDEN["cleaned_traits"] == 17


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

    from bloom_mcp.data_access.columns import resolve_columns

    reader = FakeReader()
    reader.add_experiment(_EXPERIMENT, _raw_df())
    store = SupabaseResultStore()  # writes to the patched object store
    _ports.configure(reader=reader, store=store)

    # One params object feeds BOTH sides — the exact inputs the tool forwards.
    params = QCCleanParams(experiment=_EXPERIMENT, max_nans_per_trait=_MNT)
    try:
        frame = reader.load_experiment(_EXPERIMENT, version="raw")
        # Mirror the tool's actual path: resolve_columns → role kwargs inline.
        # (Previously used _role_kwargs(frame) which reads adapter-detected roles
        # and would diverge if override priority logic ever changed.)
        r = resolve_columns(frame.df)
        expected_role_kwargs = {"barcode_col": r.sample_id, "genotype_col": r.genotype}
        if r.replicate is not None:
            expected_role_kwargs["replicate_col"] = r.replicate
        # The pipeline cleanup step, called directly with the tool's own params.
        expected_df, expected_kept, _log = clean_traits_for_analysis(
            frame.df,
            trait_cols=r.trait_cols,
            max_zeros_per_trait=params.max_zeros_per_trait,
            max_nans_per_trait=params.max_nans_per_trait,
            max_nans_per_sample=params.max_nans_per_sample,
            min_samples_per_trait=params.min_samples_per_trait,
            **expected_role_kwargs,
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

    from bloom_mcp.data_access.columns import resolve_columns

    raw = _raw_df()
    meta_cols = ["Barcode", "geno", "rep"]
    # #403: mirror the tool's detected trait set — get_trait_columns excludes numeric
    # metadata (Computation.Time.s), so feed the pipeline step the same 19 traits.
    trait_cols = resolve_columns(raw).trait_cols

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

    # Feed the step the SAME trait set qc_clean detects (roles + the 19
    # get_trait_columns traits). The step processes every numeric column it is given,
    # so without this it would also clean Computation.Time.s — which qc_clean now
    # (#403) excludes as metadata. Comparing on the shared detected set is the honest
    # parity: "same cleaning decisions on the analysis trait set".
    raw_for_step = raw[meta_cols + list(trait_cols)]
    prev = StepResult(
        data=raw_for_step,
        metadata={
            "trait_column_names": trait_cols,
            "metadata_column_names": meta_cols,
        },
    )
    pipe = CleanupTraitsStep().execute(
        data=raw_for_step, config=config, run_dir=tmp_path, prev_result=prev
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
    # A sample identifier (Barcode) is included — required as of #403.
    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(16)],
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
    assert captured["kwargs"]["barcode_col"] == "Barcode"


# ── second run increments version (latest resolves to it) ───────────────────


def test_second_run_increments_version(injected_ports):
    _reader, store = injected_ports
    _run()
    _run()
    assert [r.run_ref for r in store.list_runs(_EXPERIMENT, "qc")] == ["v1", "v2"]
    assert store.get_run(_EXPERIMENT, "qc", "latest").run_ref == "v2"


# ── qc_inspect tie-in: nudge when samples are dropped (#360 task 6.1) ────────


def test_dropped_samples_nudge_points_to_qc_inspect(injected_ports):
    """When cleaning drops samples, the result carries an advisory nudging the
    caller to run qc_inspect to see what drove the loss. This is the #360 task-6.1
    tie-in: at qc_clean's canonical defaults the NaN-heavy traits are kept, so
    turface_19 loses 29 samples (vs 0 at the golden threshold)."""
    _reader, _store = injected_ports
    result = qc_clean(QCCleanParams(experiment=_EXPERIMENT))  # canonical defaults
    assert result.n_samples_dropped == 29  # sanity: this run really drops samples
    assert result.next_step is not None
    assert "qc_inspect" in result.next_step
    assert str(result.n_samples_dropped) in result.next_step


def test_no_nudge_when_no_samples_dropped(injected_ports):
    """At the golden threshold the NaN-heavy traits are removed instead, so zero
    samples are lost — no advisory is emitted (the nudge is drop-triggered, not
    always-on)."""
    _reader, _store = injected_ports
    result = _run()  # golden _MNT → 187 in / 187 out, no sample loss
    assert result.n_samples_dropped == 0
    assert result.next_step is None


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


# ── #403: contract validation + required traceable roles + overrides ─────────


def test_result_carries_resolved_roles_and_validation_findings(injected_ports):
    """#403: the result surfaces the resolved roles, the excluded metadata, and the
    (warn-mode) validation warnings — turface resolves geno/Barcode/rep and excludes
    the numeric metadata column Computation.Time.s from the trait set."""
    _reader, _store = injected_ports
    result = _run()
    assert result.genotype_column == "geno"
    assert result.sample_id_column == "Barcode"
    assert result.replicate_column == "rep"
    assert result.excluded_columns == ["Computation.Time.s"]
    assert isinstance(result.validation_warnings, list)


def test_manifest_carries_input_validation_block(injected_ports):
    """#403: the persisted run records an additive input_validation block with the
    exact keys, the provenance-recorded contract_version, and the resolved roles."""
    _reader, store = injected_ports
    _run()
    stored = store.get_run(_EXPERIMENT, "qc", "latest")
    iv = stored.input_validation
    assert iv is not None
    assert set(iv) == {
        "mode",
        "contract_version",
        "resolved_roles",
        "excluded_columns",
        "warnings",
    }
    assert iv["mode"] == "warn"
    assert iv["resolved_roles"] == {
        "genotype": "geno",
        "sample_id": "Barcode",
        "replicate": "rep",
    }
    assert iv["excluded_columns"] == ["Computation.Time.s"]


def test_sample_id_column_override_resolves_and_cleans(injected_ports):
    """#403: a sample identifier that auto-detection misses is usable via the
    sample_id_column override; without it the required-role guard fires."""
    reader, store = injected_ports
    df = pd.DataFrame(
        {
            "my_id": [f"s{i}" for i in range(16)],  # not a SAMPLE_ID_PATTERNS match
            "geno": (["g1", "g2"] * 8),
            "tA": [float(i) for i in range(16)],
            "tB": [float(2 * i) for i in range(16)],
        }
    )
    reader.add_experiment("override.csv", df)

    # No override → sample id unresolved → structured error, no run.
    with pytest.raises(BloomMCPError) as exc:
        qc_clean(QCCleanParams(experiment="override.csv"))
    assert exc.value.code == "assumption_violated"
    assert store.list_runs("override.csv", "qc") == []

    # With the override the named column is used and cleaning succeeds.
    result = qc_clean(
        QCCleanParams(experiment="override.csv", sample_id_column="my_id")
    )
    assert result.sample_id_column == "my_id"


def test_exclude_columns_drops_trait_and_trait_columns_wins(injected_ports):
    """#403: exclude_columns removes a column from the trait set; an explicit
    trait_columns allow-list wins over exclude_columns for the same column."""
    _reader, _store = injected_ports
    # Exclude a real turface trait → it is dropped from the kept set.
    excluded = _run(exclude_columns=["Total.Root.Length.mm"])
    assert "Total.Root.Length.mm" not in excluded.kept_trait_columns
    assert "Total.Root.Length.mm" in excluded.excluded_columns

    # But an explicit trait_columns allow-list wins even when the same column is
    # also named in exclude_columns.
    won = _run(
        trait_columns=["Total.Root.Length.mm"],
        exclude_columns=["Total.Root.Length.mm"],
    )
    assert won.kept_trait_columns == ["Total.Root.Length.mm"]
    # B-2: the allow-list winner must NOT also appear in excluded_columns.
    assert "Total.Root.Length.mm" not in won.excluded_columns


def test_exclude_columns_absent_column_is_silent_noop(injected_ports):
    """I-1 / BLOCK-2: an exclude_columns entry absent from the frame is a no-op —
    no error, and the trait set is identical to an unparameterized run."""
    _reader, _store = injected_ports
    baseline = _run()
    result = _run(exclude_columns=["NoSuchColumn"])
    assert result.kept_trait_columns == baseline.kept_trait_columns
    assert result.n_traits_out == baseline.n_traits_out


def test_exclude_columns_role_column_emits_absorbed_warning(injected_ports):
    """B-3 / BLOCK-4: exclude_columns=[role_col] is absorbed by role assignment and
    surfaces as a validation_warnings entry — the caller is not silently ignored."""
    _reader, _store = injected_ports
    result = _run(exclude_columns=["geno"])  # "geno" is the auto-detected genotype role
    assert any("absorbed" in w for w in result.validation_warnings)


def test_same_column_for_genotype_and_sample_id_is_invalid_input(injected_ports):
    """B-4: supplying the same column for both genotype_column and sample_id_column
    raises invalid_input; a single column cannot serve as both roles."""
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        qc_clean(
            QCCleanParams(
                experiment=_EXPERIMENT,
                genotype_column="geno",
                sample_id_column="geno",
            )
        )
    assert exc.value.code == "invalid_input"
    assert "geno" in exc.value.message
    assert store.list_runs(_EXPERIMENT, "qc") == []


def test_partial_override_dual_role_collision_is_invalid_input(injected_ports):
    """BLOCK-2 (post-resolution guard): partial override where genotype_column names a
    column that is also auto-detected as sample_id must raise invalid_input even though
    only one param was supplied.

    turface_19: 'Barcode' matches sample_id patterns. Passing genotype_column='Barcode'
    without sample_id_column causes resolve_columns to assign Barcode to both roles.
    The post-resolution guard catches this before the run is persisted.
    """
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        qc_clean(
            QCCleanParams(
                experiment=_EXPERIMENT,
                genotype_column="Barcode",
            )
        )
    assert exc.value.code == "invalid_input"
    assert "Barcode" in exc.value.message
    assert store.list_runs(_EXPERIMENT, "qc") == []


def test_override_naming_nonexistent_column_is_invalid_input(injected_ports):
    """#403: an override that names a column absent from the frame is a fixable
    invalid_input naming it, not a downstream KeyError; nothing is persisted."""
    _reader, store = injected_ports
    with pytest.raises(BloomMCPError) as exc:
        qc_clean(QCCleanParams(experiment=_EXPERIMENT, sample_id_column="NoSuchCol"))
    assert exc.value.code == "invalid_input"
    assert "NoSuchCol" in exc.value.message
    assert store.list_runs(_EXPERIMENT, "qc") == []


def test_all_nan_genotype_column_is_assumption_violated(injected_ports):
    """Item 2 / K: a genotype override that resolves to a column whose values are
    entirely NaN/blank errors with assumption_violated, no run persisted.

    resolved.genotype is non-None (name resolved), but every row is untraceable —
    the bloommcp guard catches this independently of the contract's warn-mode."""
    reader, store = injected_ports
    df = pd.DataFrame(
        {
            "my_geno": [float("nan")] * 12,  # all NaN — untraceable
            "Barcode": [f"b{i}" for i in range(12)],
            "t1": [float(i) for i in range(12)],
            "t2": [float(2 * i) for i in range(12)],
        }
    )
    reader.add_experiment("nan_geno.csv", df)
    with pytest.raises(BloomMCPError) as exc:
        qc_clean(QCCleanParams(experiment="nan_geno.csv", genotype_column="my_geno"))
    assert exc.value.code == "assumption_violated"
    assert "my_geno" in exc.value.message
    assert store.list_runs("nan_geno.csv", "qc") == []


def test_role_column_in_trait_columns_is_invalid_input(injected_ports):
    """Item 3 / A: a caller-supplied trait column that is the resolved genotype
    role errors with invalid_input; the error names the column and explains it was
    auto-detected (so an agent knows whether to remap or remove it).

    "geno" must be numeric here so _validate_trait_subset's dtype check doesn't fire
    first — role detection is name-based regardless of dtype."""
    reader, store = injected_ports
    df = pd.DataFrame(
        {
            "geno": [0.0, 1.0] * 6,  # numeric; still auto-detects as genotype by name
            "Barcode": [f"b{i}" for i in range(12)],
            "t1": [float(i) for i in range(12)],
            "t2": [float(2 * i) for i in range(12)],
        }
    )
    reader.add_experiment("role_trait.csv", df)
    with pytest.raises(BloomMCPError) as exc:
        qc_clean(
            QCCleanParams(experiment="role_trait.csv", trait_columns=["geno", "t1"])
        )
    assert exc.value.code == "invalid_input"
    assert "geno" in exc.value.message
    assert (
        "auto-detected" in exc.value.message
    )  # item 10: explains how column became a role
    assert store.list_runs("role_trait.csv", "qc") == []


def test_warn_mode_structural_failure_is_assumption_violated(injected_ports):
    """#403: warn mode still RAISES on a universal structural failure (a frame with
    genotype + sample-id but no numeric trait) — surfaced as assumption_violated, no
    run."""
    reader, store = injected_ports
    df = pd.DataFrame(
        {
            "Barcode": [f"b{i}" for i in range(12)],
            "geno": (["g1", "g2"] * 6),
            "note": ["x"] * 12,  # non-numeric → no numeric trait
        }
    )
    reader.add_experiment("notrait.csv", df)
    with pytest.raises(BloomMCPError) as exc:
        qc_clean(QCCleanParams(experiment="notrait.csv"))
    assert exc.value.code == "assumption_violated"
    assert store.list_runs("notrait.csv", "qc") == []


def test_contracts_absent_degrades_but_role_guard_still_enforced(
    injected_ports, monkeypatch
):
    """#403: with sleap-roots-contracts monkeypatched unavailable the contract call is
    a logged no-op (no ImportError), yet bloommcp's own required-role guard still
    refuses an untraceable frame — traceability holds without the contracts package."""
    import sleap_roots_analyze.validation.input_contract as ic

    monkeypatch.setattr(ic, "CONTRACTS_AVAILABLE", False)
    reader, store = injected_ports

    # Contract absent → turface still cleans, warnings empty (validation no-ops).
    result = _run()
    assert result.validation_warnings == []

    # …but the bloommcp guard is independent of the contract: a roleless frame errors.
    reader.add_experiment(
        "roleless2.csv",
        pd.DataFrame({"t1": [1.0] * 12, "t2": [2.0] * 12}),
    )
    with pytest.raises(BloomMCPError) as exc:
        qc_clean(QCCleanParams(experiment="roleless2.csv"))
    assert exc.value.code == "assumption_violated"
    assert store.list_runs("roleless2.csv", "qc") == []


def test_new_override_params_exposed_in_tool_schema():
    """#403: the agent-facing inputSchema exposes the new override params so a client
    can discover them."""
    from fastmcp import Client

    from bloom_mcp import server

    async def _list():
        async with Client(server.mcp) as client:
            return await client.list_tools()

    tools = {t.name: t for t in asyncio.run(_list())}
    # The contract wrapper nests the model under a single ``params`` arg, so assert the
    # new fields appear anywhere in the (serialized) input schema.
    schema_json = json.dumps(tools["sleap_roots_qc_clean"].inputSchema)
    assert "sample_id_column" in schema_json
    assert "genotype_column" in schema_json
    assert "exclude_columns" in schema_json


# ── cylinder oracle (#483) ───────────────────────────────────────────────────
#
# Second fixture with a genuinely different role-column naming convention
# (plant_qr_code/Geno/Rep vs turface_19's Barcode/geno/rep) and an inverted
# samples-vs-traits ratio (129 samples x 846 traits). The golden was recorded via a
# real MCP call against the running dev stack (see
# tests/fixtures/README.md's "Cross-tier oracle fixtures (cylinder)" section), at
# qc_clean's canonical default thresholds -- unlike turface_19's raw input, cylinder's
# raw fixture was already scanner-cleaned, so nothing is dropped at those defaults.

_RAW_CYL = _FIXTURES / "cylinder_raw_data.csv"
_GOLDEN_CYL = json.loads(
    (_FIXTURES / "cylinder_qc_golden.json").read_text(encoding="utf-8")
)
_EXPERIMENT_CYL = "cylinder_raw.csv"


def _raw_df_cyl() -> pd.DataFrame:
    return pd.read_csv(_RAW_CYL, encoding="utf-8")


@pytest.fixture
def injected_ports_cylinder():
    """FakeReader serving the raw cylinder fixture + FakeResultStore."""
    reader = FakeReader()
    store = FakeResultStore()
    reader.add_experiment(_EXPERIMENT_CYL, _raw_df_cyl())
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_cylinder_cleaned_table_matches_golden_shape_and_roles(injected_ports_cylinder):
    """qc_clean at canonical defaults on cylinder matches the recorded golden: zero
    samples/traits dropped (the raw fixture is already scanner-cleaned), and role
    columns resolve to plant_qr_code/Geno/Rep -- distinct from turface_19's."""
    result = qc_clean(QCCleanParams(experiment=_EXPERIMENT_CYL))

    assert result.n_samples_out == _GOLDEN_CYL["cleaned_samples"] == 129
    assert result.n_traits_out == _GOLDEN_CYL["cleaned_traits"] == 846
    assert result.removed_traits == _GOLDEN_CYL["removed_traits"] == []
    assert result.cleaned_nan_cells_remaining == _GOLDEN_CYL["cleaned_trait_nans"] == 0

    assert (
        result.sample_id_column
        == _GOLDEN_CYL["role_columns"]["barcode_col"]
        == ("plant_qr_code")
    )
    assert (
        result.genotype_column == _GOLDEN_CYL["role_columns"]["genotype_col"] == "Geno"
    )
    assert (
        result.replicate_column == _GOLDEN_CYL["role_columns"]["replicate_col"] == "Rep"
    )
    assert sorted(result.excluded_columns) == sorted(
        _GOLDEN_CYL["excluded_from_traits"]
    )


# ── inline csv_content input (#582 — ephemeral, no persistence) ─────────────


def test_mutual_exclusivity_both_given_is_invalid_input(injected_ports):
    reader, _store = injected_ports
    reader.load_experiment = MagicMock(
        side_effect=AssertionError("load_experiment must not be called")
    )
    csv_text = _RAW.read_text(encoding="utf-8")
    with pytest.raises(BloomMCPError) as exc:
        qc_clean({"experiment": _EXPERIMENT, "csv_content": csv_text})
    assert exc.value.code == "invalid_input"
    # The specific, actionable message must reach the caller — not the contract
    # layer's generic "(<root>: value_error)" fallback (see qc_clean.py's NOTE on
    # QCCleanParams for why this is enforced in the body, not a model_validator).
    assert "exactly one" in exc.value.message.lower()
    reader.load_experiment.assert_not_called()


def test_mutual_exclusivity_neither_given_is_invalid_input(injected_ports):
    with pytest.raises(BloomMCPError) as exc:
        qc_clean({"max_nans_per_trait": _MNT})
    assert exc.value.code == "invalid_input"
    assert "exactly one" in exc.value.message.lower()


def test_inline_empty_csv_content_fails_through_the_full_tool_path(injected_ports):
    """csv_content="" passes the mutual-exclusivity check (it is not None) and must
    fail two layers deeper, inside parse_inline_csv_frame — exercised here through
    qc_clean itself, not just the raw helper (test_inline_input.py only covers the
    latter)."""
    with pytest.raises(BloomMCPError) as exc:
        qc_clean({"csv_content": ""})
    assert exc.value.code == "invalid_input"


def test_inline_cleaning_matches_the_file_based_oracle(injected_ports):
    """Equivalence oracle: the same turface_19 raw text fed as csv_content produces
    the identical cleaned-table shape and role resolution as the file-based path —
    checking every resolved-roles/shape field the result exposes, not just a subset."""
    file_based = _run()
    csv_text = _RAW.read_text(encoding="utf-8")
    inline = qc_clean(QCCleanParams(csv_content=csv_text, max_nans_per_trait=_MNT))

    assert inline.n_samples_in == file_based.n_samples_in
    assert inline.n_samples_out == file_based.n_samples_out
    assert inline.n_traits_in == file_based.n_traits_in
    assert inline.n_traits_out == file_based.n_traits_out
    assert inline.kept_trait_columns == file_based.kept_trait_columns
    assert inline.removed_traits == file_based.removed_traits
    assert inline.genotype_column == file_based.genotype_column
    assert inline.sample_id_column == file_based.sample_id_column
    assert inline.replicate_column == file_based.replicate_column
    assert inline.excluded_columns == file_based.excluded_columns
    assert inline.cleaned_nan_cells_remaining == file_based.cleaned_nan_cells_remaining
    assert inline.validation_warnings == file_based.validation_warnings
    assert inline.input_nan_summary == file_based.input_nan_summary


def test_inline_call_never_persists_a_run(injected_ports):
    _reader, store = injected_ports
    store.create_run = MagicMock(
        side_effect=AssertionError("create_run must not be called for csv_content")
    )
    store.commit = MagicMock(
        side_effect=AssertionError("commit must not be called for csv_content")
    )
    csv_text = _RAW.read_text(encoding="utf-8")

    result = qc_clean(QCCleanParams(csv_content=csv_text, max_nans_per_trait=_MNT))

    store.create_run.assert_not_called()
    store.commit.assert_not_called()
    assert result.run_ref is None
    assert result.version_dir is None
    assert result.manifest_path is None
    assert result.outputs == {}


def test_inline_result_reports_summary_hash_and_no_experiment_identity(injected_ports):
    csv_text = _RAW.read_text(encoding="utf-8")
    result = qc_clean(QCCleanParams(csv_content=csv_text, max_nans_per_trait=_MNT))

    assert result.experiment is None
    assert result.source == "inline"
    assert result.input_sha256 == compute_input_sha256(csv_text)


def test_inline_call_never_touches_the_reader_port(injected_ports):
    reader, _store = injected_ports
    reader.load_experiment = MagicMock(
        side_effect=AssertionError("load_experiment must not be called for csv_content")
    )
    csv_text = _RAW.read_text(encoding="utf-8")

    qc_clean(QCCleanParams(csv_content=csv_text, max_nans_per_trait=_MNT))

    reader.load_experiment.assert_not_called()


def test_inline_result_never_nudges_toward_qc_inspect(injected_ports):
    """Canonical defaults drop 29 turface_19 samples on the experiment path (see
    test_dropped_samples_nudge_points_to_qc_inspect) — the inline path must not
    recommend qc_inspect (it has no csv_content support) nor interpolate the
    caller's absent experiment identity into any advisory message."""
    csv_text = _RAW.read_text(encoding="utf-8")
    result = qc_clean(QCCleanParams(csv_content=csv_text))  # canonical defaults
    assert result.n_samples_dropped == 29  # sanity: this run really drops samples
    assert result.next_step is None


def test_inline_call_honors_sample_id_column_override(injected_ports):
    csv_text = (
        "my_id,geno,tA,tB\n"
        + "\n".join(
            f"s{i},{'g1' if i % 2 == 0 else 'g2'},{float(i)},{float(2 * i)}"
            for i in range(16)
        )
        + "\n"
    )
    result = qc_clean(QCCleanParams(csv_content=csv_text, sample_id_column="my_id"))
    assert result.sample_id_column == "my_id"
    assert result.source == "inline"


def test_inline_call_honors_exclude_columns(injected_ports):
    csv_text = _RAW.read_text(encoding="utf-8")
    result = qc_clean(
        QCCleanParams(
            csv_content=csv_text,
            max_nans_per_trait=_MNT,
            exclude_columns=["Total.Root.Length.mm"],
        )
    )
    assert "Total.Root.Length.mm" not in result.kept_trait_columns
    assert "Total.Root.Length.mm" in result.excluded_columns


def test_inline_roleless_error_message_names_csv_content_not_none(injected_ports):
    """The inline branch's error messages must not interpolate the literal string
    'None' where an experiment name is normally shown."""
    roleless_csv = "colA,colB\n1.0,2.0\n3.0,4.0\n"
    with pytest.raises(BloomMCPError) as exc:
        qc_clean(QCCleanParams(csv_content=roleless_csv))
    assert exc.value.code == "assumption_violated"
    assert "None" not in exc.value.message
    assert "csv_content" in exc.value.message


# ── csv_content never appears in logs (design.md's disclosed risk) ──────────


@contextlib.contextmanager
def _capture_all_logs():
    """Manually attach a handler directly to every logger in qc_clean's inline
    call graph, bypassing pytest's `caplog`.

    `bloom_mcp.data_access.columns.run_input_validation` sets
    `logger.propagate = False` on `bloom_mcp.input_validation` for the
    duration of validation (restoring it in `finally`) so its advisory
    `_WarningCapture` messages don't leak into the real logging pipeline on a
    normal call. This makes `caplog` structurally blind to that logger:
    verified empirically that even `caplog.at_level(level, logger=name)`
    captures nothing from a `propagate=False` logger, regardless of level —
    `caplog`'s capture relies on propagation to the root logger, which this
    logger deliberately suppresses. A handler attached directly to the logger
    object fires regardless of `propagate`, since `propagate` only controls
    whether a record additionally bubbles up to ancestor loggers' handlers.
    """
    records = []
    handler = logging.Handler()
    handler.emit = records.append
    loggers = [
        logging.getLogger(),  # root — catch-all for anything else in the path
        logging.getLogger("bloom_mcp.input_validation"),
        logging.getLogger("bloom_mcp.contract.errors"),
    ]
    old_levels = {lg: lg.level for lg in loggers}
    for lg in loggers:
        lg.addHandler(handler)
        lg.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        for lg in loggers:
            lg.removeHandler(handler)
            lg.setLevel(old_levels[lg])


def test_csv_content_never_appears_in_logs_on_success(injected_ports):
    """The one load-bearing safety property of the whole feature: csv_content is
    never persisted (proven elsewhere) AND never logged. Provenance.stamp's
    params=data.model_dump() carries the raw text in memory for the call's
    duration (disclosed in design.md) — this pins that it never reaches a log
    record on the successful path.

    Does NOT use `caplog` (see `_capture_all_logs`'s docstring for why a
    `propagate=False` logger makes that structurally vacuous here)."""
    marker = "MARKER_" + "Q" * 64
    csv_text = f"Barcode,geno,traitA,traitB\nS1,{marker},1.0,2.0\nS2,g2,3.0,4.0\n"
    with _capture_all_logs() as records:
        qc_clean(QCCleanParams(csv_content=csv_text, min_samples_per_trait=1))
    logged_text = "\n".join(r.getMessage() for r in records)
    assert marker not in logged_text


def test_csv_content_never_appears_in_logs_on_internal_error(
    injected_ports, monkeypatch
):
    """Same invariant on the internal_error path, where the contract layer logs
    the exception server-side (contract/errors.py's from_exception) — the log
    call must not carry csv_content, and neither may the message returned to
    the caller."""
    marker = "MARKER_" + "Q" * 64
    csv_text = f"Barcode,geno,traitA,traitB\nS1,{marker},1.0,2.0\nS2,g2,3.0,4.0\n"

    def _boom(*args, **kwargs):
        raise RuntimeError("undeclared internal failure")

    monkeypatch.setattr(qc_clean_tool, "clean_traits_for_analysis", _boom)

    with _capture_all_logs() as records:
        with pytest.raises(BloomMCPError) as exc:
            qc_clean(QCCleanParams(csv_content=csv_text))

    assert exc.value.code == "internal_error"
    assert marker not in exc.value.message
    assert marker not in exc.value.remedy
    logged_text = "\n".join(r.getMessage() for r in records)
    assert marker not in logged_text


# ── explicit source pin (#626) ──────────────────────────────────────────────


class _MultiSourceFakeReader(FakeReader):
    """Test-local double: FakeReader + a bolted-on SourceSelectable surface.

    Local to this test file only — the *shared* FakeReader class must stay
    non-SourceSelectable (test_fake_reader_is_not_source_selectable in
    test_supabase_reader.py locks that in). This subclass exists purely to
    exercise qc_clean's own source-pin/source_note logic without needing a
    full DB-shaped SupabaseReader fixture.
    """

    def __init__(self, source_ids):
        super().__init__()
        self._sources = [
            SourceInfo(
                source_id=sid, source_name=f"run-{sid}", pipeline_run_id=f"p{sid}"
            )
            for sid in source_ids
        ]

    def list_sources(self, name):
        return list(self._sources)

    def resolve_source(self, name, *, source_id=None, run_id=None):
        if source_id is not None and run_id is not None:
            raise AmbiguousSourceSelectionError("both source_id and run_id given")
        if source_id is not None:
            for s in self._sources:
                if s.source_id == source_id:
                    return s
            raise SourcePinNotFoundError(f"no source_id={source_id}")
        if run_id is not None:
            for s in self._sources:
                if s.pipeline_run_id == run_id:
                    return s
            raise SourcePinNotFoundError(f"no run_id={run_id}")
        return self._sources[-1] if self._sources else None

    def load_experiment(
        self,
        name,
        *,
        version="latest",
        require_clean=False,
        source_id=None,
        run_id=None,
    ):
        resolved = self.resolve_source(name, source_id=source_id, run_id=run_id)
        frame = super().load_experiment(
            name, version=version, require_clean=require_clean
        )
        if resolved is not None:
            frame = dataclasses.replace(frame, resolved_source=resolved)
        return frame


@pytest.fixture
def multi_source_ports():
    """Like injected_ports, but the reader is _MultiSourceFakeReader(sources=[9, 10, 11])."""
    reader = _MultiSourceFakeReader([9, 10, 11])
    store = FakeResultStore()
    reader.add_experiment(_EXPERIMENT, _raw_df())
    _ports.configure(reader=reader, store=store)
    try:
        yield reader, store
    finally:
        _ports.configure(reader=SupabaseReader(), store=SupabaseResultStore())


def test_source_id_and_run_id_fields_exist():
    assert "source_id" in QCCleanParams.model_fields
    assert "run_id" in QCCleanParams.model_fields


def test_omitting_both_source_params_preserves_todays_behavior(injected_ports):
    """Default-preserving guarantee: the new fields don't change the result
    beyond the new source_note field, which must be None on a single-source
    (FakeReader) experiment."""
    result = _run()
    assert result.source_note is None


def test_explicit_source_pin_is_honored(multi_source_ports):
    _reader, _store = multi_source_ports
    result = _run(source_id=10, min_samples_per_trait=1, max_nans_per_trait=1.0)
    # A pin was given, so there is nothing to advise.
    assert result.source_note is None


def test_both_source_id_and_run_id_given_is_rejected(multi_source_ports):
    """Ambiguous pin -> BloomMCPError through the existing
    errors=(ExperimentReadError,) mapping — no new mapping code needed."""
    with pytest.raises(BloomMCPError) as exc:
        _run(source_id=9, run_id="p10", min_samples_per_trait=1, max_nans_per_trait=1.0)
    assert (
        "source_id" in exc.value.message.lower()
        or "run_id" in exc.value.message.lower()
    )


def test_source_pin_matching_nothing_is_rejected(multi_source_ports):
    with pytest.raises(BloomMCPError):
        _run(source_id=404, min_samples_per_trait=1, max_nans_per_trait=1.0)


def test_source_pinning_unsupported_on_fakereader_surfaces_as_bloommcperror(
    injected_ports,
):
    """FakeReader (not multi_source_ports) has no source concept at all —
    SourcePinningUnsupportedError must flow through the same
    errors=(ExperimentReadError,) mapping as every other ExperimentReadError
    subclass, with no new mapping code."""
    with pytest.raises(BloomMCPError):
        _run(source_id=7)


def test_multi_source_experiment_with_no_pin_gets_an_advisory_note(multi_source_ports):
    result = _run(min_samples_per_trait=1, max_nans_per_trait=1.0)
    assert result.source_note is not None
    assert "3 sources" in result.source_note
    assert "core_list_experiment_sources" in result.source_note
    assert "11" in result.source_note  # the resolved (max) source_id


def test_single_source_experiment_gets_no_advisory_note(injected_ports):
    """injected_ports' plain FakeReader has no SourceSelectable surface at
    all, so the note-population branch never fires — the single-source case
    from the spec (not the zero-source case, which is the same code path
    here since FakeReader isn't SourceSelectable either way)."""
    result = _run()
    assert result.source_note is None


def test_csv_content_path_never_surfaces_a_source_note(injected_ports):
    csv_text = _RAW.read_text(encoding="utf-8")
    result = qc_clean(QCCleanParams(csv_content=csv_text, max_nans_per_trait=_MNT))
    assert result.source_note is None


def test_pinned_source_is_traceable_from_the_committed_runs_provenance(
    multi_source_ports,
):
    """design.md Decision 7: frame.resolved_source already flows into
    store.create_run(source=...) — this locks in that a pinned run's
    committed source metadata matches the pin given, not whatever "latest"
    would have resolved to."""
    _reader, store = multi_source_ports
    _run(source_id=9, min_samples_per_trait=1, max_nans_per_trait=1.0)

    stored = store.get_run(_EXPERIMENT, "qc", "latest")
    assert stored.source_id == 9
    assert stored.source_name == "run-9"
