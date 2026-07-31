"""Unit tests for the shared column resolver (#403).

``resolve_columns`` is bloommcp's single source of truth for role matching + trait
detection: role-name matching lives here, trait detection delegates to
``sleap_roots_analyze.get_trait_columns`` (so numeric metadata like
``Computation.Time.s`` is excluded). Both the read adapters (via the
``detect_columns`` shim) and ``qc_clean`` resolve columns through it.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from bloom_mcp.data_access.columns import (
    GENOTYPE_PATTERNS,
    REPLICATE_PATTERNS,
    SAMPLE_ID_PATTERNS,
    resolve_columns,
)

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
_RAW = _FIXTURES / "turface_19_raw_data.csv"


def _turface() -> pd.DataFrame:
    return pd.read_csv(_RAW, encoding="utf-8")


def test_resolves_turface_roles_and_detects_19_traits():
    r = resolve_columns(_turface())
    assert (r.genotype, r.sample_id, r.replicate) == ("geno", "Barcode", "rep")
    # 20 numeric candidates -> 19 traits (get_trait_columns drops the "time" column).
    assert len(r.trait_cols) == 19


def test_numeric_metadata_excluded_from_traits():
    r = resolve_columns(_turface())
    assert "Computation.Time.s" not in r.trait_cols
    assert "Computation.Time.s" in r.excluded_cols
    # roles are never traits either
    for role in (r.genotype, r.sample_id, r.replicate):
        assert role not in r.trait_cols


def test_overrides_force_named_roles_and_exclusions():
    df = pd.DataFrame(
        {
            "my_id": [f"s{i}" for i in range(6)],  # not a SAMPLE_ID_PATTERNS match
            "line": (["a", "b"] * 3),  # not a GENOTYPE_PATTERNS match
            "tA": [float(i) for i in range(6)],
            "tB": [float(2 * i) for i in range(6)],
        }
    )
    # Without overrides neither role auto-detects.
    bare = resolve_columns(df)
    assert bare.genotype is None and bare.sample_id is None

    # Overrides force the named columns; exclude_columns drops a trait.
    r = resolve_columns(
        df,
        sample_id_column="my_id",
        genotype_column="line",
        exclude_columns=["tB"],
    )
    assert r.sample_id == "my_id"
    assert r.genotype == "line"
    assert r.trait_cols == ["tA"]
    assert "tB" in r.excluded_cols


def test_role_pattern_lists_live_here_not_in_experiment_utils():
    # The lists moved to data_access.columns (#403); experiment_utils no longer defines them.
    import bloom_mcp.experiment_utils as eu

    assert not hasattr(eu, "GENOTYPE_PATTERNS")
    assert "geno" in GENOTYPE_PATTERNS
    assert "rep" in REPLICATE_PATTERNS
    assert "barcode" in SAMPLE_ID_PATTERNS


def test_detect_columns_shim_delegates_to_resolve_columns():
    # The reader's detect_columns is a thin shim: same trait/role outcome, dict shape.
    from bloom_mcp.experiment_utils import detect_columns

    df = _turface()
    detected = detect_columns(df)
    r = resolve_columns(df)
    assert detected["trait_cols"] == r.trait_cols
    assert detected["genotype_col"] == r.genotype
    assert detected["sample_id_col"] == r.sample_id
    assert detected["replicate_col"] == r.replicate
    assert "Computation.Time.s" not in detected["trait_cols"]


def test_run_input_validation_captures_advisory_warnings(monkeypatch):
    """F / B2: advisory warnings emitted through the injected logger are captured.

    Monkeypatches validate_entry_input to emit one warning via the injected logger
    and asserts run_input_validation returns it in the list. Guards the getLogger
    vs Logger() regression — a broken logger means validation_warnings is silently
    empty even when warnings exist.
    """
    import sleap_roots_analyze.validation as _val

    from bloom_mcp.data_access.columns import ResolvedColumns, run_input_validation

    def _warn_via_logger(df, *, columns, mode, additional_exclude, logger):
        logger.warning("advisory: replicate column has only one unique value")

    monkeypatch.setattr(_val, "validate_entry_input", _warn_via_logger)

    df = pd.DataFrame(
        {
            "geno": ["a"] * 6,
            "Barcode": [f"b{i}" for i in range(6)],
            "t1": [float(i) for i in range(6)],
        }
    )
    resolved = ResolvedColumns(
        genotype="geno",
        sample_id="Barcode",
        replicate=None,
        trait_cols=["t1"],
        excluded_cols=[],
        metadata_cols=["geno", "Barcode"],
    )
    warnings = run_input_validation(df, resolved)
    assert len(warnings) == 1
    assert "replicate" in warnings[0]


def test_run_input_validation_no_handler_accumulation(monkeypatch):
    """B-1: calling run_input_validation twice in succession must not accumulate
    handlers on the singleton logger — the second call must return the same warning
    count as the first (not 2x).

    Guards the try/finally + removeHandler fix: deleting removeHandler from the
    finally block would double-count warnings on the second call.
    """
    import sleap_roots_analyze.validation as _val

    from bloom_mcp.data_access.columns import ResolvedColumns, run_input_validation

    def _warn_once(df, *, columns, mode, additional_exclude, logger):
        logger.warning("advisory: replicate column has only one unique value")

    monkeypatch.setattr(_val, "validate_entry_input", _warn_once)

    df = pd.DataFrame(
        {
            "geno": ["a"] * 6,
            "Barcode": [f"b{i}" for i in range(6)],
            "t1": [float(i) for i in range(6)],
        }
    )
    resolved = ResolvedColumns(
        genotype="geno",
        sample_id="Barcode",
        replicate=None,
        trait_cols=["t1"],
        excluded_cols=[],
        metadata_cols=["geno", "Barcode"],
    )
    w1 = run_input_validation(df, resolved)
    w2 = run_input_validation(df, resolved)
    assert len(w1) == len(w2) == 1


def test_resolves_canonical_sample_id_over_later_metadata_patterns():
    """bloom#551: a SupabaseReader-produced frame already has a literal
    ``sample_id`` column (renamed from ``plant_qr_code``) *and* separate
    ``plant_id`` / ``scan_id`` metadata columns that also match
    SAMPLE_ID_PATTERNS. The already-canonical column must win, or the
    caller-side rename-to-"sample_id" step fails because that name is
    already taken by a different column.
    """
    df = pd.DataFrame(
        {
            "genotype": ["a", "b", "c"],
            "sample_id": ["p1", "p2", "p3"],
            "plant_id": [101, 102, 103],
            "scan_id": [201, 202, 203],
            "t1": [1.0, 2.0, 3.0],
        }
    )
    r = resolve_columns(df)
    assert r.sample_id == "sample_id"
    assert r.genotype == "genotype"


def test_degenerate_frames_do_not_raise():
    # Empty frame → no traits, no roles, no raise.
    empty = resolve_columns(pd.DataFrame())
    assert empty.trait_cols == [] and empty.genotype is None

    # All-metadata (no numeric trait) frame → empty trait_cols.
    meta_only = resolve_columns(pd.DataFrame({"geno": ["a", "b"], "note": ["x", "y"]}))
    assert meta_only.trait_cols == []

    # A frame whose only numeric column is the excluded metadata column.
    only_time = resolve_columns(
        pd.DataFrame({"geno": ["a", "b"], "Computation.Time.s": [1.0, 2.0]})
    )
    assert only_time.trait_cols == []
    assert "Computation.Time.s" in only_time.excluded_cols
