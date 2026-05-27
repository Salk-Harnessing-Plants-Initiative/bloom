"""Integration tests for the phenotyping-supabase-tools.
"""

import pytest

pytestmark = pytest.mark.integration


def test_view_returns_one_row_per_experiment_wave_trait_tuple(pg_conn):
    """The view has exactly one row per distinct (experiment_id, wave_id, trait_name)
    tuple from the underlying join — no duplicates, no missing combinations."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM public.cyl_trait_by_experiment_wave;")
        view_count = cur.fetchone()[0]

        cur.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT DISTINCT e.id AS experiment_id, w.id AS wave_id, ct.id AS trait_id
                FROM public.cyl_scan_traits t
                JOIN public.cyl_traits      ct ON t.trait_id = ct.id
                JOIN public.cyl_scans       s  ON t.scan_id  = s.id
                JOIN public.cyl_plants      p  ON s.plant_id = p.id
                JOIN public.cyl_waves       w  ON p.wave_id  = w.id
                JOIN public.cyl_experiments e  ON w.experiment_id = e.id
                WHERE e.deleted = FALSE
            ) AS distinct_tuples;
            """
        )
        expected_count = cur.fetchone()[0]

    if view_count == 0 and expected_count == 0:
        pytest.skip("no seeded cyl trait data — view exists but DB has no rows to validate against")

    assert view_count == expected_count, (
        f"view has {view_count} rows but the underlying join distinct count is {expected_count}"
    )


def test_view_aggregates_match_hand_computed(pg_conn):
    """For one (experiment_id, wave_id, trait_name) tuple in the view, the
    aggregates (n, mean, std, min_value, max_value) match what a direct
    aggregate over the underlying tables produces."""
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT experiment_id, wave_id, trait_name, n, mean, std, min_value, max_value
            FROM public.cyl_trait_by_experiment_wave
            LIMIT 1;
            """
        )
        row = cur.fetchone()
        if row is None:
            pytest.skip("no seeded cyl trait data — cannot validate aggregates")
        exp_id, wave_id, trait_name, view_n, view_mean, view_std, view_min, view_max = row

        cur.execute(
            """
            SELECT
                COUNT(t.value)  AS n,
                AVG(t.value)    AS mean,
                STDDEV(t.value) AS std,
                MIN(t.value)    AS min_value,
                MAX(t.value)    AS max_value
            FROM public.cyl_scan_traits t
            JOIN public.cyl_traits      ct ON t.trait_id = ct.id
            JOIN public.cyl_scans       s  ON t.scan_id  = s.id
            JOIN public.cyl_plants      p  ON s.plant_id = p.id
            JOIN public.cyl_waves       w  ON p.wave_id  = w.id
            JOIN public.cyl_experiments e  ON w.experiment_id = e.id
            WHERE e.id = %s AND w.id = %s AND ct.name = %s AND e.deleted = FALSE;
            """,
            (exp_id, wave_id, trait_name),
        )
        expected_n, expected_mean, expected_std, expected_min, expected_max = cur.fetchone()

    assert view_n == expected_n, f"view n={view_n} vs hand n={expected_n}"
    assert abs(view_mean - float(expected_mean)) < 1e-6, f"mean drift: {view_mean} vs {expected_mean}"
    if view_std is not None and expected_std is not None:
        assert abs(view_std - float(expected_std)) < 1e-6, f"std drift: {view_std} vs {expected_std}"
    assert abs(view_min - float(expected_min)) < 1e-6, f"min drift: {view_min} vs {expected_min}"
    assert abs(view_max - float(expected_max)) < 1e-6, f"max drift: {view_max} vs {expected_max}"


def test_view_excludes_deleted_experiments(pg_conn):
    """Soft-deleting an experiment removes all its rows from the view.
    Uses a transaction without commit so the UPDATE rolls back at teardown."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT experiment_id FROM public.cyl_trait_by_experiment_wave LIMIT 1;"
        )
        row = cur.fetchone()
        if row is None:
            pytest.skip("no view rows to soft-delete")
        exp_id = row[0]

        cur.execute(
            "UPDATE public.cyl_experiments SET deleted = TRUE WHERE id = %s;",
            (exp_id,),
        )

        cur.execute(
            "SELECT COUNT(*) FROM public.cyl_trait_by_experiment_wave WHERE experiment_id = %s;",
            (exp_id,),
        )
        post_delete_count = cur.fetchone()[0]

    assert post_delete_count == 0, (
        f"view still has {post_delete_count} rows for soft-deleted experiment {exp_id} — "
        f"the WHERE e.deleted = FALSE filter in the view isn't taking effect"
    )

    # Transaction not committed; pg_conn fixture teardown closes the connection,
    # which rolls back the UPDATE. No persistent change.


# --- TestPostgRESTAccess (task 1.6) ---


def test_view_accessible_via_postgrest(api, anon_key):
    """PostgREST exposes the view at /rest/v1/cyl_trait_by_experiment_wave."""
    status, body = api("/api/rest/v1/cyl_trait_by_experiment_wave?limit=5", api_key=anon_key)
    assert status == 200, f"expected 200, got {status}: {body}"
    assert isinstance(body, list), f"expected JSON array, got {type(body).__name__}: {body}"
    # body may be empty in fresh-seed environments; the contract here is "the
    # view is exposed and the request succeeds," not "data exists."


def test_view_filters_by_experiment_id(api, anon_key):
    """The view supports PostgREST's `in.()` filter on experiment_id — the
    pattern the compare tool will use to scope to two experiments."""
    status, body = api(
        "/api/rest/v1/cyl_trait_by_experiment_wave?experiment_id=in.(1,2)&limit=10",
        api_key=anon_key,
    )
    assert status == 200, f"expected 200, got {status}: {body}"
    assert isinstance(body, list)
    if body:
        for row in body:
            assert row["experiment_id"] in (1, 2), (
                f"filter returned row with experiment_id={row['experiment_id']} outside requested set"
            )
