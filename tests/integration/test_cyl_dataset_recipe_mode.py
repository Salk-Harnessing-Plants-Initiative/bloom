"""
Integration tests for create_cyl_dataset, including add-cyl-trait-recipe-key's recipe
mode (migration 4). Spec: cyl-datasets.

The characterization test pins how source mode behaved before this change, so the
recipe-mode rewrite cannot silently change it. LOCAL ONLY: every test rolls back.
"""

import json
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.cyl_recipe_helpers import acl_set  # noqa: E402
from tests.integration.test_cyl_read_path import (  # noqa: E402
    _register_trait,
    _seed_experiment,
    _seed_scan_in,
)

DATASET_ACL = {
    (role, "EXECUTE")
    for role in ("PUBLIC", "anon", "authenticated", "service_role", "postgres")
}


def _plant_of(cur, scan_id):
    cur.execute("SELECT plant_id FROM cyl_scans WHERE id = %s", (scan_id,))
    return cur.fetchone()[0]


def _source(cur, rows):
    """A source with the given {scan_id: value} rows of one trait. Returns its id."""
    cur.execute(
        "INSERT INTO cyl_trait_sources (name) VALUES (%s) RETURNING id",
        (f"ds-{uuid.uuid4().hex[:8]}",),
    )
    sid = cur.fetchone()[0]
    trait = _register_trait(cur, "dataset-fixture-A")
    for scan_id, value in rows.items():
        cur.execute(
            "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) "
            "VALUES (%s, %s, %s, %s)",
            (scan_id, sid, trait, value),
        )
    return sid


def _qc_set(cur, plant_id):
    name = f"qc-{uuid.uuid4().hex[:8]}"
    cur.execute("INSERT INTO cyl_qc_sets (name) VALUES (%s) RETURNING id", (name,))
    set_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_qc_codes (plant_id, value) VALUES (%s, 'flagged') RETURNING id",
        (plant_id,),
    )
    cur.execute(
        "INSERT INTO cyl_qc_set_codes (set_id, code_id) VALUES (%s, %s)",
        (set_id, cur.fetchone()[0]),
    )
    return name


def _create(
    cur, experiment_id, *, trait_source_id=None, qc=None, timepoints=None, **kw
):
    name = f"dataset-{uuid.uuid4().hex[:10]}"
    args = {
        "name": name,
        "experiment_id": experiment_id,
        "trait_source_id": trait_source_id,
        "qc_set_name": json.dumps({"name": qc}) if qc is not None else None,
        "timepoints": json.dumps(timepoints) if timepoints is not None else None,
        **kw,
    }
    named = ", ".join(
        f"{k} => %s::json" if k in ("qc_set_name", "timepoints") else f"{k} => %s"
        for k in args
    )
    cur.execute(f"SELECT create_cyl_dataset({named})", list(args.values()))
    return name


def _frozen(cur, name):
    cur.execute(
        "SELECT t.scan_id, t.source_id FROM cyl_dataset_traits dt "
        "JOIN cyl_datasets d ON d.id = dt.dataset_id "
        "JOIN cyl_scan_traits t ON t.id = dt.trait_id WHERE d.name = %s",
        (name,),
    )
    return sorted(cur.fetchall())


def test_source_mode_characterization(pg_conn):
    with pg_conn.cursor() as cur:
        exp, wave = _seed_experiment(cur)
        s7a = _seed_scan_in(cur, wave, n_images=0, plant_age_days=7)[0]
        s7b = _seed_scan_in(cur, wave, n_images=0, plant_age_days=7)[0]
        s9 = _seed_scan_in(cur, wave, n_images=0, plant_age_days=9)[0]
        src = _source(cur, {s7a: 1.0, s7b: 2.0, s9: 3.0})
        other = _source(cur, {s7a: 4.0})
        qc = _qc_set(cur, _plant_of(cur, s7b))

        filtered = _create(cur, exp, trait_source_id=src, qc=qc, timepoints=[7])
        assert _frozen(cur, filtered) == [(s7a, src)]

        unknown_qc = _create(
            cur, exp, trait_source_id=src, qc="no-such-set", timepoints=[7]
        )
        assert _frozen(cur, unknown_qc) == sorted([(s7a, src), (s7b, src)])

        everything = _create(cur, exp, trait_source_id=src)
        assert _frozen(cur, everything) == sorted([(s7a, src), (s7b, src), (s9, src)])
        assert other not in {s for _, s in _frozen(cur, everything)}

        cur.execute(
            "SELECT trait_source_id, experiment_id FROM cyl_datasets WHERE name = %s",
            (everything,),
        )
        assert cur.fetchone() == (src, exp)
        cur.execute(
            "SELECT oid::regprocedure::text FROM pg_proc WHERE proname = 'create_cyl_dataset'"
        )
        (sig,) = cur.fetchone()
        assert acl_set(cur, sig) == DATASET_ACL
