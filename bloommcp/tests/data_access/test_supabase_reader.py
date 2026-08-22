"""SupabaseReader adapter — exercised on the in-memory storage + DB boundaries."""

from __future__ import annotations

import pandas as pd
import pytest

from bloom_mcp.contract import Provenance
from bloom_mcp.data_access import (
    AmbiguousRunIdError,
    AmbiguousSampleIdentityError,
    AmbiguousSourceSelectionError,
    CleanedVersionRequiredError,
    DuplicateTraitReadingError,
    ExperimentNotFoundError,
    ExperimentReadError,
    FakeReader,
    MultipleScansPerPlantError,
    RawSourced,
    SourcePinNotFoundError,
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
        "scan_id",
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


def test_pin_matching_nothing_raises_source_pin_not_found(
    fake_supabase_storage, fake_supabase_db
):
    """A pin matching nothing for an experiment that DOES exist is
    SourcePinNotFoundError, distinct from ExperimentNotFoundError -- a caller
    can tell "wrong experiment" from "right experiment, stale pin"."""
    experiment_id = _seed_two_plant_experiment(fake_supabase_db)
    reader = SupabaseReader()
    with pytest.raises(SourcePinNotFoundError):
        reader.load_experiment(str(experiment_id), source_id=404)
    with pytest.raises(SourcePinNotFoundError):
        reader.load_experiment(str(experiment_id), run_id="no-such-run")


def test_pin_given_for_a_nonexistent_experiment_raises_not_found(
    fake_supabase_storage, fake_supabase_db
):
    """A pin given for an experiment that does NOT exist at all is
    ExperimentNotFoundError, not SourcePinNotFoundError -- the experiment
    itself, not just the pin, is the problem."""
    reader = SupabaseReader()
    with pytest.raises(ExperimentNotFoundError):
        reader.load_experiment("999999", source_id=1)


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
    # bloom#637: trait_columns's own staleness, threaded from the RPC's n_traits_updated_at.
    assert summary.trait_columns_updated_at == "2026-01-01T00:00:00+00:00"
    # The whole point of this change: one bulk RPC call, not one per experiment.
    assert fake_supabase_db.rpc_calls.count("get_experiment_summary_counts") == 1
    assert "get_experiment_traits" not in fake_supabase_db.rpc_calls
    # The discovery -> read round trip: the printed filename must itself load.
    SupabaseReader().load_experiment(summary.filename)


def test_list_experiments_reports_zero_counts_for_experiment_with_no_traits(
    fake_supabase_storage, fake_supabase_db
):
    """An experiment that exists in cyl_experiments but has no matching trait
    rows is listed with rows=0/trait_columns=0, not excluded -- the bulk
    RPC's result set has no row for it at all, so list_experiments() must
    default the missing entry to zero rather than dropping the experiment."""
    _seed_two_plant_experiment(fake_supabase_db, experiment_id=42)
    fake_supabase_db.seed_experiment(43, "no traits yet")
    summaries = {s.filename: s for s in SupabaseReader().list_experiments()}
    assert set(summaries) == {"42", "43"}
    assert summaries["43"].rows == 0
    assert summaries["43"].trait_columns == 0
    # Missing from the bulk RPC's result entirely -- defaults to None, not a fabricated timestamp.
    assert summaries["43"].trait_columns_updated_at is None


def test_list_experiments_raises_when_summary_counts_rpc_fails(
    fake_supabase_storage, fake_supabase_db
):
    """A bulk get_experiment_summary_counts failure has no per-experiment
    granularity to fail-open into (unlike the old per-experiment loop) --
    it must surface as a structured ExperimentReadError, not an empty or
    partial list."""
    _seed_two_plant_experiment(fake_supabase_db, experiment_id=42)
    fake_supabase_db.fail_rpc(
        "get_experiment_summary_counts",
        RuntimeError("connection reset by peer at 10.0.0.5:5432"),
    )
    with pytest.raises(ExperimentReadError) as exc_info:
        SupabaseReader().list_experiments()
    assert "10.0.0.5" not in str(exc_info.value)


def test_get_experiment_summary_counts_rpc_honors_a_pinned_experiment_id(
    fake_supabase_storage, fake_supabase_db
):
    """`list_experiments()` only ever calls this RPC unpinned, but its signature
    exists so a future source-pinning caller can reuse it pinned to one
    experiment_id_ -- prove the fake (and therefore the contract a real caller
    would rely on) actually honors that pin, not just the all-NULL case."""
    import bloom_mcp.supabase_client as _sc

    _seed_two_plant_experiment(fake_supabase_db, experiment_id=42)
    _seed_two_plant_experiment(fake_supabase_db, experiment_id=43)

    rows = _sc.call_rpc(
        "get_experiment_summary_counts",
        {"experiment_id_": 42, "source_id_": None, "run_id_": None},
    )

    assert {r["experiment_id"] for r in rows} == {42}


def test_supabase_reader_no_longer_satisfies_raw_sourced(
    fake_supabase_storage, fake_supabase_db
):
    assert isinstance(SupabaseReader(), SourceSelectable)
    assert not isinstance(SupabaseReader(), RawSourced)


def test_resolved_source_set_for_raw_read_pinned_or_not(
    fake_supabase_storage, fake_supabase_db
):
    """PR #557 review: `frame.resolved_source` is the value a caller should
    stamp into provenance — not a fresh, independent re-resolution at commit
    time, which can race ahead of what the frame's data actually is."""
    experiment_id = _seed_two_plant_experiment(fake_supabase_db)
    reader = SupabaseReader()

    unpinned = reader.load_experiment(str(experiment_id))
    assert unpinned.resolved_source.source_id == 9

    pinned = reader.load_experiment(str(experiment_id), source_id=9)
    assert pinned.resolved_source.source_id == 9


def test_resolved_source_is_none_for_cleaned_tier_read(
    fake_supabase_storage, fake_supabase_db
):
    """A cleaned-tier read never touches the raw DB tier, so it must not carry
    a source identity — recording one would misattribute lineage the read
    never consulted."""
    experiment_id = _seed_two_plant_experiment(fake_supabase_db)
    store = SupabaseResultStore()
    raw = pd.DataFrame({"Genotype": ["g"], "trait": [1.0]})
    run = store.create_run(
        experiment=str(experiment_id),
        tool_class="qc",
        provenance=Provenance.stamp(tool="run_qc_workflow", params={}),
    )
    (run.staging_dir / "_cleaned.csv").write_text(raw.to_csv(index=False))
    store.commit(run, {"_cleaned.csv": "_cleaned.csv"})

    frame = SupabaseReader().load_experiment(str(experiment_id))
    assert frame.source.endswith("_cleaned")
    assert frame.resolved_source is None
    assert frame.available_source_count is None


def test_available_source_count_reflects_the_real_multi_source_read(
    fake_supabase_storage, fake_supabase_db, seed_multi_source_experiment
):
    """PR #644 review: every prior multi-source test went through the hand-rolled
    _MultiSourceFakeReader double, which reimplements the resolution logic rather
    than exercising the real adapter -- design.md's own Decision 5 says multi-source
    *data* tests should use the monkeypatched-SupabaseReader boundary instead. This
    is that direct coverage: the real SupabaseReader.load_experiment, against a real
    (fake-DB-backed) list_experiment_trait_sources RPC, actually populates
    available_source_count -- and does so from a SINGLE list_sources-backing RPC
    call, proving the no-redundant-round-trip claim against the real adapter too,
    not just the test double that stands in for it elsewhere."""
    experiment_id = seed_multi_source_experiment(fake_supabase_db, 42, [9, 10, 11])
    reader = SupabaseReader()

    frame = reader.load_experiment(str(experiment_id))

    assert frame.available_source_count == 3
    assert frame.resolved_source.source_id == 11  # unpinned resolves the max id
    assert fake_supabase_db.rpc_calls.count("list_experiment_trait_sources") == 1


def test_resolved_source_reflects_load_time_not_a_later_resolution(
    fake_supabase_storage, fake_supabase_db
):
    """A frame's resolved_source is fixed at load time. A source landing
    *after* the frame was loaded must not retroactively change what an
    already-loaded frame reports — the whole point of capturing it on the
    frame instead of re-resolving "the current latest" at commit time."""
    experiment_id = _seed_two_plant_experiment(fake_supabase_db)
    reader = SupabaseReader()

    frame = reader.load_experiment(str(experiment_id))
    assert frame.resolved_source.source_id == 9

    # A newer source lands after the frame was loaded (e.g. a reprocessing
    # run completing between this tool's load and its eventual commit).
    fake_supabase_db.seed_sources(
        experiment_id,
        [{"source_id": 10, "source_name": "run-b", "pipeline_run_id": "p10"}],
    )
    assert reader.resolve_source(str(experiment_id)).source_id == 10
    # The already-loaded frame is unaffected — it still reports what it
    # actually read, not "whatever is latest now".
    assert frame.resolved_source.source_id == 9


def test_multiple_scans_per_plant_raises_structured_error(
    fake_supabase_storage, fake_supabase_db
):
    """A plant with two distinct scan_ids in the resolved source has no
    defined column layout in this pivot — a structured error, not silently
    keyed by (scan_id, plant_id)."""
    experiment_id = 21
    fake_supabase_db.seed_experiment(experiment_id, "rescanned plant")
    rows = [
        _trait_row(
            plant_id=1,
            scan_id=501,
            qr_code="QRX",
            accession="acc-a",
            wave=1,
            trait_name="root_length",
            trait_value=1.0,
        ),
        _trait_row(
            plant_id=1,
            scan_id=502,
            qr_code="QRX",
            accession="acc-a",
            wave=1,
            trait_name="root_length",
            trait_value=1.1,
        ),
    ]
    fake_supabase_db.seed_traits(experiment_id, rows)
    with pytest.raises(MultipleScansPerPlantError) as exc_info:
        SupabaseReader().load_experiment(str(experiment_id))
    # The internal plant_id must not leak into the agent-facing message --
    # only the qr_code, consistent with AmbiguousSampleIdentityError's policy.
    assert "QRX" in str(exc_info.value)
    assert "plant 1 " not in str(exc_info.value)


# --- Second review round: run_id ambiguity, duplicate trait rows, mixed sources ---


def test_ambiguous_run_id_raises(fake_supabase_storage, fake_supabase_db):
    """pipeline_run_id carries no DB uniqueness constraint (only idempotency_key
    is enforced) -- a run_id pin matching more than one source must raise, not
    silently pick one of the matches."""
    experiment_id = 60
    fake_supabase_db.seed_experiment(experiment_id, "shared pipeline run id")
    fake_supabase_db.seed_traits(
        experiment_id,
        [
            _trait_row(
                plant_id=1,
                scan_id=801,
                qr_code="QRA",
                accession="acc-a",
                wave=1,
                trait_name="root_length",
                trait_value=1.0,
                source_id=1,
            ),
            _trait_row(
                plant_id=2,
                scan_id=802,
                qr_code="QRB",
                accession="acc-b",
                wave=1,
                trait_name="root_length",
                trait_value=2.0,
                source_id=2,
            ),
        ],
    )
    fake_supabase_db.seed_sources(
        experiment_id,
        [
            {"source_id": 1, "source_name": "run-a", "pipeline_run_id": "shared"},
            {"source_id": 2, "source_name": "run-b", "pipeline_run_id": "shared"},
        ],
    )
    reader = SupabaseReader()
    with pytest.raises(AmbiguousRunIdError):
        reader.resolve_source(str(experiment_id), run_id="shared")
    with pytest.raises(AmbiguousRunIdError):
        reader.load_experiment(str(experiment_id), run_id="shared")


def test_duplicate_trait_value_for_same_plant_raises(
    fake_supabase_storage, fake_supabase_db
):
    """A duplicate (plant_id, trait_name) pair within one resolved source has
    no DB constraint preventing it. pivot_table's aggfunc='first' would
    silently keep an arbitrary one and drop the rest -- the same class of risk
    this module already guards against for sample_id/multi-scan collisions."""
    experiment_id = 70
    fake_supabase_db.seed_experiment(experiment_id, "duplicate trait row")
    fake_supabase_db.seed_traits(
        experiment_id,
        [
            _trait_row(
                plant_id=1,
                scan_id=901,
                qr_code="QRD",
                accession="acc-a",
                wave=1,
                trait_name="root_length",
                trait_value=1.0,
            ),
            _trait_row(
                plant_id=1,
                scan_id=901,
                qr_code="QRD",
                accession="acc-a",
                wave=1,
                trait_name="root_length",
                trait_value=2.0,
            ),
        ],
    )
    with pytest.raises(DuplicateTraitReadingError):
        SupabaseReader().load_experiment(str(experiment_id))


def test_unpinned_read_never_mixes_two_genuinely_distinct_sources(
    fake_supabase_storage, fake_supabase_db
):
    """D2's 'one source per frame, never mixed' claim, exercised against a
    fixture where the SAME plant+trait genuinely differs across two sources --
    not just two single-source fixtures compared separately. An unpinned read
    must resolve to exactly one source's value, never a blend."""
    experiment_id = 50
    fake_supabase_db.seed_experiment(experiment_id, "reprocessed experiment")
    fake_supabase_db.seed_traits(
        experiment_id,
        [
            _trait_row(
                plant_id=1,
                scan_id=701,
                qr_code="QRM",
                accession="acc-a",
                wave=1,
                trait_name="root_length",
                trait_value=1.0,
                source_id=1,
            ),
            _trait_row(
                plant_id=1,
                scan_id=701,
                qr_code="QRM",
                accession="acc-a",
                wave=1,
                trait_name="root_length",
                trait_value=99.0,
                source_id=2,
            ),
        ],
    )
    fake_supabase_db.seed_sources(
        experiment_id,
        [
            {"source_id": 1, "source_name": "run-a", "pipeline_run_id": "p1"},
            {"source_id": 2, "source_name": "run-b", "pipeline_run_id": "p2"},
        ],
    )
    frame = SupabaseReader().load_experiment(str(experiment_id))
    # Unpinned resolves to the experiment-wide max source_id (2) -- source 1's
    # value must not appear, and no aggregation/blend of the two is acceptable.
    assert frame.resolved_source.source_id == 2
    assert frame.df["root_length"].iloc[0] == 99.0


def test_null_trait_value_becomes_nan_not_error(
    fake_supabase_storage, fake_supabase_db
):
    """trait_value is a nullable Postgres column; a NULL must pivot to NaN,
    not crash the read."""
    experiment_id = 80
    fake_supabase_db.seed_experiment(experiment_id, "null trait value")
    fake_supabase_db.seed_traits(
        experiment_id,
        [
            _trait_row(
                plant_id=1,
                scan_id=1001,
                qr_code="QRN",
                accession="acc-a",
                wave=1,
                trait_name="root_length",
                trait_value=None,
            )
        ],
    )
    frame = SupabaseReader().load_experiment(str(experiment_id))
    assert frame.trait_cols == ["root_length"]
    assert frame.df["root_length"].isna().all()


def test_pin_with_default_version_still_reaches_raw_tier_when_no_cleaned_version(
    fake_supabase_storage, fake_supabase_db
):
    """A pin passed with the default version='latest' must still resolve
    against the raw tier when no cleaned version exists yet -- only an
    ACTUALLY-resolved cleaned version should reject a pin (see the test
    below), not merely a non-'raw' version value."""
    experiment_id = _seed_two_plant_experiment(fake_supabase_db)
    frame = SupabaseReader().load_experiment(str(experiment_id), source_id=9)
    assert frame.source == "raw"
    assert frame.resolved_source.source_id == 9


def test_source_pin_is_rejected_when_a_cleaned_version_resolves_first(
    fake_supabase_storage, fake_supabase_db
):
    """A source_id/run_id pin only applies to the DB-backed raw tier. Silently
    returning the cleaned frame instead would let a caller believe their pin
    was honored when it never was."""
    experiment_id = _seed_two_plant_experiment(fake_supabase_db)
    store = SupabaseResultStore()
    raw = pd.DataFrame({"Genotype": ["g"], "trait": [1.0]})
    run = store.create_run(
        experiment=str(experiment_id),
        tool_class="qc",
        provenance=Provenance.stamp(tool="run_qc_workflow", params={}),
    )
    (run.staging_dir / "_cleaned.csv").write_text(raw.to_csv(index=False))
    store.commit(run, {"_cleaned.csv": "_cleaned.csv"})

    reader = SupabaseReader()
    with pytest.raises(AmbiguousSourceSelectionError):
        reader.load_experiment(str(experiment_id), source_id=9)
    with pytest.raises(AmbiguousSourceSelectionError):
        reader.load_experiment(str(experiment_id), run_id="p9")

    # version="raw" explicitly bypasses the cleaned tier, so the pin is honored.
    frame = reader.load_experiment(str(experiment_id), source_id=9, version="raw")
    assert frame.source == "raw"


def test_raw_version_with_require_clean_raises_contradiction_not_missing_clean(
    fake_supabase_storage, fake_supabase_db
):
    """version='raw' + require_clean=True is a self-contradictory request, not
    'no cleaned dataset found' -- version='raw' never even looks for one."""
    experiment_id = _seed_two_plant_experiment(fake_supabase_db)
    with pytest.raises(CleanedVersionRequiredError, match="contradictory"):
        SupabaseReader().load_experiment(
            str(experiment_id), version="raw", require_clean=True
        )


def test_rpc_failure_surfaces_as_experiment_read_error_not_raw(
    fake_supabase_storage, fake_supabase_db
):
    """A raw Supabase/network exception from call_rpc must not escape the
    ExperimentReadError contract -- it may embed backend detail (a SQL
    message, connection info) that must never reach an agent-facing message."""
    experiment_id = _seed_two_plant_experiment(fake_supabase_db)
    fake_supabase_db.fail_rpc(
        "get_experiment_traits",
        RuntimeError("connection reset by peer at 10.0.0.5:5432"),
        experiment_id=experiment_id,
    )
    with pytest.raises(ExperimentReadError) as exc_info:
        SupabaseReader().load_experiment(str(experiment_id))
    assert "10.0.0.5" not in str(exc_info.value)


def test_list_experiments_excludes_a_malformed_row(
    fake_supabase_storage, fake_supabase_db
):
    """A malformed cyl_experiments row (missing 'id') must exclude only that
    row from the listing, not crash the whole call -- the same per-item
    fail-open treatment as an RPC failure."""
    _seed_two_plant_experiment(fake_supabase_db, experiment_id=42)
    fake_supabase_db.tables["cyl_experiments"].append({"name": "no id field"})
    summaries = SupabaseReader().list_experiments()
    assert {s.filename for s in summaries} == {"42"}


def test_fake_reader_is_not_source_selectable():
    """FakeReader has no source-versioned substrate; it must not satisfy
    SourceSelectable (unlike SupabaseReader)."""
    assert not isinstance(FakeReader(), SourceSelectable)
