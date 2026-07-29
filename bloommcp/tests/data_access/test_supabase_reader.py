"""SupabaseReader adapter — exercised on the in-memory storage + DB boundaries."""

from __future__ import annotations

import pandas as pd
import pytest

from bloom_mcp.contract import Provenance
from bloom_mcp.data_access import (
    AmbiguousSampleIdentityError,
    AmbiguousSourceSelectionError,
    ExperimentNotFoundError,
    RawSourced,
    SourceSelectable,
    SupabaseReader,
)
from bloom_mcp.result_store import SupabaseResultStore


def _trait_row(
    *,
    plant_id: int,
    scan_id: int,
    qr_code: str,
    accession: str,
    wave: int,
    trait_name: str,
    trait_value: float,
    source_id=None,
    pipeline_run_id=None,
    plant_age_days: int = 10,
    date_scanned: str = "2026-07-01",
    germ_day: int = 0,
) -> dict:
    return {
        "scan_id": scan_id,
        "date_scanned": date_scanned,
        "plant_age_days": plant_age_days,
        "wave_number": wave,
        "plant_id": plant_id,
        "germ_day": germ_day,
        "plant_qr_code": qr_code,
        "accession_name": accession,
        "trait_name": trait_name,
        "source_id": source_id,
        "trait_value": trait_value,
    }


def _seed_two_plant_experiment(fake_supabase_db, experiment_id=42):
    fake_supabase_db.seed_experiment(experiment_id, "cyl exp 42")
    rows = []
    for plant_id, qr, acc in [(1, "QR1", "acc-a"), (2, "QR2", "acc-b")]:
        for trait, value in [
            ("root_length", 1.0 * plant_id),
            ("root_angle", 2.0 * plant_id),
        ]:
            rows.append(
                _trait_row(
                    plant_id=plant_id,
                    scan_id=100 + plant_id,
                    qr_code=qr,
                    accession=acc,
                    wave=1,
                    trait_name=trait,
                    trait_value=value,
                    source_id=9,
                )
            )
    fake_supabase_db.seed_traits(experiment_id, rows)
    fake_supabase_db.seed_sources(
        experiment_id,
        [{"source_id": 9, "source_name": "run-a", "pipeline_run_id": "p9"}],
    )
    return experiment_id


def test_resolves_versioned_cleaned_then_raw(fake_supabase_storage, fake_supabase_db):
    experiment_id = _seed_two_plant_experiment(fake_supabase_db)
    reader = SupabaseReader()

    # No cleaned version yet -> resolves the DB-direct raw tier.
    frame = reader.load_experiment(str(experiment_id))
    assert frame.source == "raw"
    assert set(frame.trait_cols) == {"root_length", "root_angle"}

    # Commit a cleaned version through the store, then it resolves first.
    store = SupabaseResultStore()
    raw = pd.DataFrame({"Genotype": ["g"], "trait": [1.0]})
    run = store.create_run(
        experiment=str(experiment_id),
        tool_class="qc",
        provenance=Provenance.stamp(tool="run_qc_workflow", params={}),
    )
    (run.staging_dir / "_cleaned.csv").write_text(raw.to_csv(index=False))
    store.commit(run, {"_cleaned.csv": "_cleaned.csv"})

    resolved = reader.load_experiment(str(experiment_id))
    assert resolved.source.endswith("_cleaned")
    assert "trait" in resolved.trait_cols


def test_non_numeric_name_raises_not_found_not_local_fallback(
    fake_supabase_storage, fake_supabase_db
):
    reader = SupabaseReader()
    with pytest.raises(ExperimentNotFoundError):
        reader.load_experiment("nope.csv")


def test_unknown_numeric_experiment_raises_not_found(
    fake_supabase_storage, fake_supabase_db
):
    reader = SupabaseReader()
    with pytest.raises(ExperimentNotFoundError):
        reader.load_experiment("999999")


def test_load_experiment_wide_pivot_with_canonical_roles(
    fake_supabase_storage, fake_supabase_db
):
    experiment_id = _seed_two_plant_experiment(fake_supabase_db)
    frame = SupabaseReader().load_experiment(str(experiment_id))

    assert frame.genotype_col == "genotype"
    assert frame.sample_id_col == "sample_id"
    assert set(frame.trait_cols) == {"root_length", "root_angle"}
    assert set(frame.metadata_cols) == {
        "wave",
        "plant_age_days",
        "date_scanned",
        "plant_id",
    }
    assert sorted(frame.df["sample_id"]) == ["QR1", "QR2"]
    assert sorted(frame.df["genotype"]) == ["acc-a", "acc-b"]


def test_empty_trait_rows_is_valid_not_not_found(
    fake_supabase_storage, fake_supabase_db
):
    experiment_id = 7
    fake_supabase_db.seed_experiment(experiment_id, "no traits yet")
    frame = SupabaseReader().load_experiment(str(experiment_id))
    assert frame.trait_cols == []


def test_concurrent_source_and_run_pin_rejected_before_any_rpc_call(
    fake_supabase_storage, fake_supabase_db
):
    experiment_id = _seed_two_plant_experiment(fake_supabase_db)
    fake_supabase_db.fail_rpc(
        "list_experiment_trait_sources",
        AssertionError("should not be called"),
        experiment_id=experiment_id,
    )
    reader = SupabaseReader()
    with pytest.raises(AmbiguousSourceSelectionError):
        reader.load_experiment(str(experiment_id), source_id=9, run_id="p9")
    with pytest.raises(AmbiguousSourceSelectionError):
        reader.resolve_source(str(experiment_id), source_id=9, run_id="p9")


def test_pin_matching_nothing_raises_not_found(fake_supabase_storage, fake_supabase_db):
    experiment_id = _seed_two_plant_experiment(fake_supabase_db)
    reader = SupabaseReader()
    with pytest.raises(ExperimentNotFoundError):
        reader.load_experiment(str(experiment_id), source_id=404)
    with pytest.raises(ExperimentNotFoundError):
        reader.load_experiment(str(experiment_id), run_id="no-such-run")


def test_sample_id_collision_across_waves_raises(
    fake_supabase_storage, fake_supabase_db
):
    experiment_id = 5
    fake_supabase_db.seed_experiment(experiment_id, "colliding qr codes")
    rows = [
        _trait_row(
            plant_id=1,
            scan_id=201,
            qr_code="DUP",
            accession="acc-a",
            wave=1,
            trait_name="root_length",
            trait_value=1.0,
        ),
        _trait_row(
            plant_id=2,
            scan_id=202,
            qr_code="DUP",
            accession="acc-b",
            wave=2,
            trait_name="root_length",
            trait_value=2.0,
        ),
    ]
    fake_supabase_db.seed_traits(experiment_id, rows)
    with pytest.raises(AmbiguousSampleIdentityError):
        SupabaseReader().load_experiment(str(experiment_id))


def test_legacy_only_data_resolves_to_no_source(
    fake_supabase_storage, fake_supabase_db
):
    experiment_id = 11
    fake_supabase_db.seed_experiment(experiment_id, "legacy only")
    fake_supabase_db.seed_traits(
        experiment_id,
        [
            _trait_row(
                plant_id=1,
                scan_id=301,
                qr_code="QRL",
                accession="acc-legacy",
                wave=1,
                trait_name="root_length",
                trait_value=1.0,
                source_id=None,
            )
        ],
    )
    reader = SupabaseReader()
    assert reader.list_sources(str(experiment_id)) == []
    assert reader.resolve_source(str(experiment_id)) is None
    frame = reader.load_experiment(str(experiment_id))
    assert frame.trait_cols == ["root_length"]


def test_list_sources_and_resolve_source_pin(fake_supabase_storage, fake_supabase_db):
    experiment_id = _seed_two_plant_experiment(fake_supabase_db)
    reader = SupabaseReader()
    sources = reader.list_sources(str(experiment_id))
    assert [s.source_id for s in sources] == [9]

    resolved = reader.resolve_source(str(experiment_id), source_id=9)
    assert resolved.source_id == 9
    resolved_by_run = reader.resolve_source(str(experiment_id), run_id="p9")
    assert resolved_by_run.source_id == 9
    unpinned = reader.resolve_source(str(experiment_id))
    assert unpinned.source_id == 9


def test_list_experiments_enumerates_database_experiments(
    fake_supabase_storage, fake_supabase_db
):
    _seed_two_plant_experiment(fake_supabase_db, experiment_id=42)
    summaries = SupabaseReader().list_experiments()
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.filename == "42"
    assert summary.stem == "42"
    assert summary.rows == 2
    assert summary.trait_columns == 2
    assert summary.genotype_col == "genotype"
    assert summary.sample_id_col == "sample_id"
    # The discovery -> read round trip: the printed filename must itself load.
    SupabaseReader().load_experiment(summary.filename)


def test_list_experiments_excludes_a_failing_experiment(
    fake_supabase_storage, fake_supabase_db
):
    _seed_two_plant_experiment(fake_supabase_db, experiment_id=42)
    fake_supabase_db.seed_experiment(43, "broken experiment")
    fake_supabase_db.fail_rpc(
        "get_experiment_traits", RuntimeError("boom"), experiment_id=43
    )
    summaries = SupabaseReader().list_experiments()
    assert {s.filename for s in summaries} == {"42"}


def test_supabase_reader_no_longer_satisfies_raw_sourced(
    fake_supabase_storage, fake_supabase_db
):
    assert isinstance(SupabaseReader(), SourceSelectable)
    assert not isinstance(SupabaseReader(), RawSourced)
