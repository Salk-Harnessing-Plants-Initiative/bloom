"""LocalReader adapter — the opt-in fully-local input reader (no Supabase).

Exercised with a real ``local`` object-storage backend + local input dir on temp
paths; no live Supabase and (by construction) no ``supabase.create_client``.
"""

from __future__ import annotations

import io
import re
import warnings
from pathlib import Path

import pandas as pd
import pytest

import bloom_mcp.experiment_utils as eu
import bloom_mcp.storage_backend as sb
from bloom_mcp.data_access import (
    CleanedVersionRequiredError,
    ExperimentNotFoundError,
    ExperimentReadError,
    LocalReader,
    SourceSelectable,
)

_RAW = "Genotype,trait_a,trait_b\ng1,1.0,3.0\ng2,2.0,4.0\ng3,3.0,5.0\n"


@pytest.fixture
def local_env(monkeypatch, tmp_path):
    """Fully-local: BLOOM_STORAGE_BACKEND=local + a local input dir + a store root."""
    inp = tmp_path / "input"
    inp.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "local")
    monkeypatch.setenv("BLOOM_STORAGE_LOCAL_ROOT", str(store))
    monkeypatch.setenv("BLOOM_EXPERIMENT_LOCAL_ROOT", str(inp))
    monkeypatch.setenv("BLOOM_STORAGE_URL", "http://localhost/output")
    sb.reset_backend_for_tests()
    try:
        yield inp, store
    finally:
        sb.reset_backend_for_tests()


def _seed_cleaned(experiment: str, df: pd.DataFrame) -> None:
    """Commit a versioned cleaned output through the (local) store."""
    from bloom_mcp.contract import Provenance
    from bloom_mcp.result_store import SupabaseResultStore

    st = SupabaseResultStore()
    run = st.create_run(
        experiment=experiment,
        tool_class="qc",
        provenance=Provenance.stamp(tool="run_qc_workflow", params={}),
    )
    (run.staging_dir / "_cleaned.csv").write_text(df.to_csv(index=False))
    st.commit(run, {"_cleaned.csv": "_cleaned.csv"})


# ── raw load + roles + no deprecation ───────────────────────────────────────


def test_reads_raw_with_roles_and_no_deprecation(local_env):
    inp, _ = local_env
    (inp / "exp.csv").write_text(_RAW)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        frame = LocalReader().load_experiment("exp.csv")

    assert frame.source == "raw"
    assert set(frame.trait_cols) == {"trait_a", "trait_b"}
    assert frame.genotype_col == "Genotype"
    assert not any(issubclass(w.category, DeprecationWarning) for w in caught)


def test_list_experiments_enumerates_and_empty(local_env):
    inp, _ = local_env
    assert LocalReader().list_experiments() == []
    (inp / "a.csv").write_text(_RAW)
    (inp / "b.csv").write_text(_RAW)
    summaries = LocalReader().list_experiments()
    assert {s.filename for s in summaries} == {"a.csv", "b.csv"}


# ── version selection + signalling ──────────────────────────────────────────


def test_unknown_experiment_not_found_no_path_leak(local_env):
    inp, _ = local_env
    with pytest.raises(ExperimentNotFoundError) as exc:
        LocalReader().load_experiment("nope.csv")
    # no host path leaked into the caller-facing message
    assert str(inp) not in str(exc.value)


def test_require_clean_with_no_cleaned_raises(local_env):
    inp, _ = local_env
    (inp / "exp.csv").write_text(_RAW)
    with pytest.raises(CleanedVersionRequiredError):
        LocalReader().load_experiment("exp.csv", require_clean=True)


def test_versioned_cleaned_resolves_from_local_store(local_env):
    inp, _ = local_env
    (inp / "exp.csv").write_text(_RAW)
    _seed_cleaned("exp.csv", pd.read_csv(io.StringIO(_RAW)))

    frame = LocalReader().load_experiment("exp.csv", require_clean=True)
    assert frame.source.startswith("v1")
    assert set(frame.trait_cols) == {"trait_a", "trait_b"}


def test_manifest_read_failure_is_caller_safe_not_raw(local_env, monkeypatch):
    """A storage failure during the manifest lookup itself (not a missing file --
    an actual read error) must surface as ExperimentReadError, not propagate as a
    raw, unhandled exception. LocalReader reaches the same shared
    `_resolve_versioned_cleaned`/`_resolve_one_class` code SupabaseReader does, via
    `experiment_utils.load_experiment_data` -- this proves the #586 fix covers this
    adapter too, not just SupabaseReader."""
    import bloom_mcp.manifest.manifest as manifest_mod

    inp, _ = local_env
    (inp / "exp.csv").write_text(_RAW)
    _seed_cleaned("exp.csv", pd.read_csv(io.StringIO(_RAW)))

    def _boom(prefix: str):
        raise RuntimeError("connection reset by peer at 10.0.0.5:5432")

    monkeypatch.setattr(manifest_mod, "list_prefix", _boom)

    with pytest.raises(ExperimentReadError) as exc:
        LocalReader().load_experiment("exp.csv")
    assert "10.0.0.5" not in str(exc.value)


def test_require_clean_does_not_honor_stale_legacy(local_env, monkeypatch, tmp_path):
    """A stale un-versioned legacy cleaned CSV must NOT satisfy require_clean —
    it carries no manifest/hash lineage and may not match the current input."""
    inp, _ = local_env
    (inp / "exp.csv").write_text(_RAW)
    out = tmp_path / "legacy_out"
    (out / "qc_exp").mkdir(parents=True)
    (out / "qc_exp" / "exp_cleaned.csv").write_text(_RAW)
    monkeypatch.setattr(eu, "OUTPUT_DIR", out)

    with pytest.raises(CleanedVersionRequiredError):
        LocalReader().load_experiment("exp.csv", require_clean=True)


# ── containment guard ───────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["../x.csv", "/etc/passwd", "sub/x.csv", "..", "", "."])
def test_name_escaping_root_is_rejected(local_env, bad):
    with pytest.raises(ExperimentReadError):
        LocalReader().load_experiment(bad)


def test_symlink_escape_is_rejected(local_env, tmp_path):
    inp, _ = local_env
    outside = tmp_path / "secret.csv"
    outside.write_text(_RAW)
    link = inp / "link.csv"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable on this platform")
    with pytest.raises(ExperimentReadError):
        LocalReader().load_experiment("link.csv")


# ── structural Supabase-independence + reader/store coupling ────────────────


def test_local_reader_module_has_no_supabase_import():
    import bloom_mcp.data_access.local_reader as m

    src = Path(m.__file__).read_text()
    assert not re.search(r"^\s*(?:import|from)\b.*supabase", src, re.M)


def test_local_reader_requires_local_backend(monkeypatch):
    monkeypatch.setenv("BLOOM_STORAGE_BACKEND", "supabase")
    sb.reset_backend_for_tests()
    with pytest.raises(RuntimeError, match="local"):
        LocalReader()


# ── provenance capability ───────────────────────────────────────────────────


def test_raw_source_path(local_env):
    inp, _ = local_env
    (inp / "exp.csv").write_text(_RAW)
    reader = LocalReader()
    assert reader.raw_source_path("exp.csv") == inp / "exp.csv"
    assert reader.raw_source_path("absent.csv") is None
    assert reader.raw_source_path("../escape.csv") is None


def test_local_reader_is_not_source_selectable(local_env):
    """LocalReader has no source-versioned substrate; unlike SupabaseReader,
    it must not satisfy SourceSelectable."""
    assert not isinstance(LocalReader(), SourceSelectable)


# ── #573: foreign catalog surfaces as ForeignCatalogError ────────────────────


def _foreignize_local_manifest(store_root, stem: str, tool_class: str = "qc") -> None:
    """Hand-patch the on-disk sentinel — the only way to manufacture a foreign
    catalog (write_manifest always re-stamps from active_backend_name(), and a
    backend flip reads a physically different, empty store)."""
    import json

    path = store_root / "bloommcp_output" / f"{tool_class}_{stem}" / "manifest.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["storage_backend"] == "local"
    raw["storage_backend"] = "supabase"
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_foreign_catalog_raises_foreign_catalog_error_not_clean_required(
    local_env, monkeypatch
):
    """Spec: "require_clean surfaces the mismatch as ForeignCatalogError in
    both readers" — NOT CleanedVersionRequiredError's "run the QC workflow
    first", which would invite committing fresh runs on top of the foreign
    catalog."""
    from bloom_mcp.data_access import ForeignCatalogError

    monkeypatch.delenv("BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST", raising=False)
    inp, store = local_env
    (inp / "exp.csv").write_text(_RAW)
    _seed_cleaned("exp.csv", pd.read_csv(io.StringIO(_RAW)))
    _foreignize_local_manifest(store, "exp")

    with pytest.raises(ForeignCatalogError) as exc:
        LocalReader().load_experiment("exp.csv", require_clean=True)

    assert not isinstance(exc.value, CleanedVersionRequiredError)
    msg = str(exc.value)
    assert "'supabase'" in msg and "'local'" in msg
    assert str(store) not in msg  # no host path leak


def test_foreign_catalog_never_falls_through_to_raw(local_env, monkeypatch):
    """Spec: "A foreign catalog is a hard resolution error, never a
    fall-through" — with a raw input present, a plain (require_clean=False)
    load still raises rather than silently serving the raw frame."""
    from bloom_mcp.data_access import ForeignCatalogError

    monkeypatch.delenv("BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST", raising=False)
    inp, store = local_env
    (inp / "exp.csv").write_text(_RAW)
    _seed_cleaned("exp.csv", pd.read_csv(io.StringIO(_RAW)))
    _foreignize_local_manifest(store, "exp")

    with pytest.raises(ForeignCatalogError):
        LocalReader().load_experiment("exp.csv")


def test_escape_hatch_restores_resolution_with_warning_trail(
    local_env, monkeypatch, caplog
):
    """Spec: "The escape hatch restores resolution with a warning trail" —
    BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST=1 serves the cleaned version and the
    per-read warning records are present."""
    import logging

    monkeypatch.setenv("BLOOM_STORAGE_ALLOW_FOREIGN_MANIFEST", "1")
    inp, store = local_env
    (inp / "exp.csv").write_text(_RAW)
    _seed_cleaned("exp.csv", pd.read_csv(io.StringIO(_RAW)))
    _foreignize_local_manifest(store, "exp")

    with caplog.at_level(logging.WARNING, logger="bloom_mcp.manifest.manifest"):
        caplog.clear()
        frame = LocalReader().load_experiment("exp.csv", require_clean=True)

    assert frame.source.startswith("v1")
    warnings_ = [
        r for r in caplog.records if "foreign catalog" in r.getMessage()
    ]
    assert warnings_, "expected at least one per-read warning under the hatch"
