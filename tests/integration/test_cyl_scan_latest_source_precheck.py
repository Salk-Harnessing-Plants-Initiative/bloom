"""
Characterization test for the Bloom UI pipeline dialog's pre-check (change `add-cyl-pipeline-ui`,
bloom#15 design §10).

Before submitting a run, the dialog reports "K of N already have pipeline results" and, in its
details, "L more scans have only traits without a recorded source". It gets K and L from one
`cyl_scan_latest_source` read per chunk of scan ids:

    K = scans whose row has max_source_id IS NOT NULL
    L = scans whose row has max_source_id IS NULL: source-less legacy traits only, or (rarely,
        admin-only) every trait deleted after the row was created
    a scan that never had traits has no row

This pins those semantics against the real producer: rows are written by the maintaining trigger
on `cyl_scan_traits`, not inserted by hand. It also pins that `bloom_user`, the role the browser
reads as, can see them.

(characterization) `cyl_scan_latest_source` and its trigger already exist
(20260817130000_create_cyl_scan_latest_source.sql), so this passes on first run. It guards the
dialog's copy against a change in those semantics.

LOCAL ONLY: `pg_conn` connects as `supabase_admin`; the test rolls back.
"""

import itertools

import pytest

psycopg = pytest.importorskip("psycopg")

_uniq = itertools.count(1)


def _scans(cur, n: int) -> list[int]:
    cur.execute(
        "INSERT INTO species (common_name) VALUES (%s) RETURNING id",
        (f"pc-sp-{next(_uniq)}",),
    )
    species_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_experiments (name, species_id) VALUES (%s, %s) RETURNING id",
        (f"pc-exp-{next(_uniq)}", species_id),
    )
    exp_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_waves (experiment_id, number) VALUES (%s, 1) RETURNING id",
        (exp_id,),
    )
    wave_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO accessions (name) VALUES (%s) RETURNING id",
        (f"pc-acc-{next(_uniq)}",),
    )
    accession_id = cur.fetchone()[0]
    ids = []
    for _ in range(n):
        cur.execute(
            "INSERT INTO cyl_plants (wave_id, accession_id) VALUES (%s, %s) RETURNING id",
            (wave_id, accession_id),
        )
        plant_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO cyl_scans (plant_id, date_scanned, plant_age_days) "
            "VALUES (%s, '2026-01-01', 10) RETURNING id",
            (plant_id,),
        )
        ids.append(cur.fetchone()[0])
    return ids


def _source(cur) -> int:
    cur.execute(
        "INSERT INTO cyl_trait_sources (name) VALUES (%s) RETURNING id",
        (f"pc-src-{next(_uniq)}",),
    )
    return cur.fetchone()[0]


def _trait(cur, scan_id: int, source_id: int | None) -> None:
    cur.execute(
        "INSERT INTO cyl_scan_traits (scan_id, source_id, value) VALUES (%s, %s, 1.0)",
        (scan_id, source_id),
    )


def test_precheck_k_and_l_as_bloom_user(pg_conn):
    with pg_conn.cursor() as cur:
        with_result, mixed, legacy_only, all_deleted, no_traits = _scans(cur, 5)
        source_id = _source(cur)

        _trait(cur, with_result, source_id)
        # A source-less trait alongside a sourced one still counts as a pipeline result.
        _trait(cur, mixed, None)
        _trait(cur, mixed, source_id)
        _trait(cur, legacy_only, None)
        # Deleting every trait leaves the maintained row behind with max_source_id NULL, so this
        # scan counts in L even though it now has no traits at all. It only happens through
        # bloom_admin break-glass deletes, which is why the dialog copy says "typically".
        _trait(cur, all_deleted, source_id)
        cur.execute("DELETE FROM cyl_scan_traits WHERE scan_id = %s", (all_deleted,))

        cur.execute("SET LOCAL ROLE bloom_user")
        cur.execute(
            "SELECT scan_id, max_source_id FROM cyl_scan_latest_source WHERE scan_id = ANY(%s)",
            ([with_result, mixed, legacy_only, all_deleted, no_traits],),
        )
        rows = dict(cur.fetchall())

    pg_conn.rollback()

    k = {scan for scan, max_source in rows.items() if max_source is not None}
    l_scans = {scan for scan, max_source in rows.items() if max_source is None}
    assert k == {with_result, mixed}
    assert l_scans == {legacy_only, all_deleted}
    assert (
        no_traits not in rows
    ), "a scan that never had traits must have no cyl_scan_latest_source row"
