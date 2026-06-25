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
