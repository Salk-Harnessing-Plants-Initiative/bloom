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


# --------------------------------------------------------------------------- #
# add-cyl-trait-recipe-key: recipe mode (migration 4)
# --------------------------------------------------------------------------- #

import os  # noqa: E402
import re  # noqa: E402
import urllib.error  # noqa: E402

from tests.integration.cyl_recipe_helpers import (  # noqa: E402
    REPO_ROOT,
    apply_recipe_rollbacks,
    migration,
    sql_body,
)
from tests.integration.test_cyl_trait_recipes_read import Fixture, _coverage  # noqa: E402

SIX_ARG = "public.create_cyl_dataset(text,bigint,bigint,json,json,text)"
FIVE_ARG = "public.create_cyl_dataset(text,bigint,bigint,json,json)"
BASE_BODY = (
    REPO_ROOT
    / "supabase"
    / "migrations"
    / ("20240904033106_create_fix_create_cyl_dataset_function_again.sql")
)


def _dataset_row(cur, name):
    cur.execute(
        "SELECT trait_source_id, recipe_key FROM cyl_datasets WHERE name = %s", (name,)
    )
    return cur.fetchone()


def _by_scan(frozen):
    out = {}
    for scan_id, source_id in frozen:
        out.setdefault(scan_id, set()).add(source_id)
    return out


def test_frozen_rows_do_not_change(pg_conn):
    with pg_conn.cursor() as cur:
        exp, wave = _seed_experiment(cur)
        scan = _seed_scan_in(cur, wave, n_images=0)[0]
        src = _source(cur, {scan: 1.0})
        name = _create(cur, exp, trait_source_id=src)
        before = _frozen(cur, name)
        _source(cur, {scan: 9.0})
        assert _frozen(cur, name) == before


def test_source_mode_records_recipe(pg_conn):
    with pg_conn.cursor() as cur:
        fx = Fixture(cur)
        name = _create(cur, fx.E1, trait_source_id=fx.src[45])
        assert _dataset_row(cur, name) == (fx.src[45], fx.K["K1"])


def test_recipe_mode_takes_each_scans_source_of_the_recipe(pg_conn):
    with pg_conn.cursor() as cur:
        fx = Fixture(cur)
        name = _create(cur, fx.E1, recipe_key=fx.K["K1"])
        assert _dataset_row(cur, name) == (None, fx.K["K1"])
        got = _by_scan(_frozen(cur, name))
        s, src = fx.s, fx.src
        assert got == {
            s["a"]: {src[50]},
            s["b"]: {src[11]},
            s["c"]: {src[12]},
            s["e"]: {src[45]},
            s["f"]: {src[14]},
            s["j2"]: {src[15]},
            s["k"]: {src[16]},
        }


def test_recipe_mode_matches_recipe_read_and_coverage(pg_conn):
    with pg_conn.cursor() as cur:
        fx = Fixture(cur)
        for key in (fx.K["K1"], fx.K["K2"], "unattributed", fx.K["legacy:L"]):
            name = _create(cur, fx.E1, recipe_key=key)
            cur.execute(
                "SELECT scan_id, source_id FROM get_experiment_traits(%s, NULL, NULL, %s, NULL)",
                (fx.E1, key),
            )
            read = sorted(cur.fetchall())
            assert _frozen(cur, name) == read, key
            cov = _coverage(cur, [fx.E1], recipe_key=key)
            included = {
                sid: c["source_id"]
                for sid, c in cov.items()
                if c["status"] == "included"
            }
            assert {k: {v} for k, v in included.items()} == _by_scan(
                _frozen(cur, name)
            ), key


def test_recipe_mode_unattributed_and_legacy(pg_conn):
    with pg_conn.cursor() as cur:
        fx = Fixture(cur)
        unattributed = _create(cur, fx.E1, recipe_key="unattributed")
        assert _by_scan(_frozen(cur, unattributed)) == {
            fx.s["f"]: {None},
            fx.s["h"]: {None},
        }
        by_key = _create(cur, fx.E1, recipe_key=fx.K["legacy:L"])
        by_src = _create(cur, fx.E1, trait_source_id=fx.L)
        assert _frozen(cur, by_key) == _frozen(cur, by_src)


def test_recipe_mode_timepoints_and_qc(pg_conn):
    with pg_conn.cursor() as cur:
        fx = Fixture(cur)
        assert (
            _frozen(cur, _create(cur, fx.E1, recipe_key=fx.K["K1"], timepoints=[7]))
            == []
        )
        qc = _qc_set(cur, _plant_of(cur, fx.s["a"]))
        got = _by_scan(_frozen(cur, _create(cur, fx.E1, recipe_key=fx.K["K1"], qc=qc)))
        assert fx.s["a"] not in got and fx.s["b"] in got


@pytest.mark.parametrize(
    "kw",
    [
        {},
        {"trait_source_id": "SRC", "recipe_key": "unattributed"},
        {"recipe_key": "f" * 64},
    ],
)
def test_selector_errors(pg_conn, kw):
    with pg_conn.cursor() as cur:
        fx = Fixture(cur)
        kw = {k: (fx.src[45] if v == "SRC" else v) for k, v in kw.items()}
        cur.execute("SELECT count(*) FROM cyl_datasets")
        before = cur.fetchone()[0]
        cur.execute("SAVEPOINT sel")
        with pytest.raises(psycopg.errors.RaiseException):
            _create(cur, fx.E1, **kw)
        cur.execute("ROLLBACK TO SAVEPOINT sel")
        cur.execute("SELECT count(*) FROM cyl_datasets")
        assert cur.fetchone()[0] == before


def test_dataset_function_properties(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT pronargs, prosecdef, proowner::regrole::text, proconfig FROM pg_proc "
            "WHERE proname = 'create_cyl_dataset'"
        )
        rows = cur.fetchall()
        assert len(rows) == 1
        nargs, secdef, owner, config = rows[0]
        assert (nargs, secdef, owner) == (6, False, "postgres")
        assert "statement_timeout=0" in (config or [])
        assert acl_set(cur, SIX_ARG) == DATASET_ACL


def _gateway(api, key, body):
    try:
        return api(
            "/api/rest/v1/rpc/create_cyl_dataset", api_key=key, method="POST", data=body
        )
    except (urllib.error.URLError, OSError) as e:
        if os.environ.get("CI"):
            pytest.fail(f"PostgREST gateway not reachable in CI: {e}")
        pytest.skip(f"PostgREST gateway not reachable ({e}); CI covers this")


@pytest.mark.parametrize(
    "extra, message",
    [
        ({}, "exactly one of trait_source_id and recipe_key"),
        ({"recipe_key": "f" * 64}, "unknown recipe_key"),
    ],
)
def test_gateway_calls_resolve_and_raise(
    api, service_role_key, pg_conninfo, extra, message
):
    name = f"gateway-{uuid.uuid4().hex[:10]}"
    body = {
        "name": name,
        "experiment_id": 1,
        "trait_source_id": None,
        "qc_set_name": None,
        "timepoints": None,
        **extra,
    }
    try:
        status, resp = _gateway(api, service_role_key, body)
        assert status == 400, (status, resp)
        text = json.dumps(resp)
        assert "PGRST202" not in text and "PGRST203" not in text
        assert message in text, resp
    finally:
        with psycopg.connect(pg_conninfo, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM cyl_dataset_traits WHERE dataset_id IN "
                "(SELECT id FROM cyl_datasets WHERE name = %s)",
                (name,),
            )
            conn.execute("DELETE FROM cyl_datasets WHERE name = %s", (name,))


def test_datasets_recipe_key_backfill_and_check(pg_conn):
    with pg_conn.cursor() as cur:
        fx = Fixture(cur)
        apply_recipe_rollbacks(cur, down_to=4)
        names = {}
        for label, src in (("pipeline", fx.src[45]), ("legacy", fx.L), ("none", None)):
            names[label] = f"bf-{uuid.uuid4().hex[:10]}"
            cur.execute(
                "INSERT INTO cyl_datasets (name, experiment_id, trait_source_id) "
                "VALUES (%s, %s, %s)",
                (names[label], fx.E1, src),
            )
        cur.execute(sql_body(migration(4)))
        expected = {"pipeline": fx.K["K1"], "legacy": fx.K["legacy:L"], "none": None}
        for label, name in names.items():
            assert _dataset_row(cur, name)[1] == expected[label], label
        cur.execute(sql_body(migration(4)))
        for label, name in names.items():
            assert _dataset_row(cur, name)[1] == expected[label], label
        for value, ok in (
            ("unattributed", True),
            ("legacy:3", True),
            ("a" * 64, True),
            ("legacy:", False),
            ("A" * 64, False),
        ):
            cur.execute("SAVEPOINT chk")
            try:
                cur.execute(
                    "UPDATE cyl_datasets SET recipe_key = %s WHERE name = %s",
                    (value, names["none"]),
                )
                cur.execute("RELEASE SAVEPOINT chk")
                assert ok, value
            except psycopg.errors.CheckViolation:
                cur.execute("ROLLBACK TO SAVEPOINT chk")
                assert not ok, value


def test_migration_4_body_is_idempotent(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(sql_body(migration(4)))
        cur.execute(sql_body(migration(4)))
        cur.execute(
            "SELECT array_agg(pronargs) FROM pg_proc WHERE proname = 'create_cyl_dataset'"
        )
        assert cur.fetchone()[0] == [6]


def test_rollback_4_restores_five_arg(pg_conn):
    notices = []
    pg_conn.add_notice_handler(lambda d: notices.append(d.message_primary))
    with pg_conn.cursor() as cur:
        fx = Fixture(cur)
        _create(cur, fx.E1, recipe_key=fx.K["K1"])
        apply_recipe_rollbacks(cur, down_to=4)
        cur.execute(
            "SELECT array_agg(pronargs) FROM pg_proc WHERE proname = 'create_cyl_dataset'"
        )
        assert cur.fetchone()[0] == [5]
        assert acl_set(cur, FIVE_ARG) == DATASET_ACL
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = 'cyl_datasets' AND column_name = 'recipe_key'"
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT prosrc FROM pg_proc WHERE oid = %s::regprocedure", (FIVE_ARG,)
        )
        restored = cur.fetchone()[0]
        base = BASE_BODY.read_text(encoding="utf-8")
        assert restored.strip() in base
    counts = [
        int(m.group(1))
        for n in notices
        if (m := re.match(r".*?(\d+) recipe-mode dataset", n))
    ]
    assert counts and counts[0] >= 1


def test_full_rollback_chain_round_trip(pg_conn):
    with pg_conn.cursor() as cur:
        apply_recipe_rollbacks(cur, down_to=1)
        cur.execute(
            "SELECT count(*) FROM pg_proc WHERE proname IN ('cyl_trait_recipe_key_v1', "
            "'cyl_trait_recipe_payload_v1', 'cyl_backfill_trait_source_recipe_identity', "
            "'list_trait_recipes', 'get_trait_recipe_coverage', '_cyl_trait_recipe_presence')"
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT pronargs FROM pg_proc WHERE proname = 'get_experiment_traits'"
        )
        assert [r[0] for r in cur.fetchall()] == [3]
        cur.execute("SELECT pronargs FROM pg_proc WHERE proname = 'create_cyl_dataset'")
        assert [r[0] for r in cur.fetchall()] == [5]
        exp, wave = _seed_experiment(cur)
        scan = _seed_scan_in(cur, wave, n_images=0)[0]
        src = _source(cur, {scan: 1.0})
        _create(cur, exp, trait_source_id=src)
        cur.execute(
            "SELECT count(*) FROM get_experiment_traits(%s, NULL, NULL)", (exp,)
        )
        assert cur.fetchone()[0] == 1
        for n in (1, 2, 3, 4):
            cur.execute(sql_body(migration(n)))
        cur.execute(
            "SELECT pronargs FROM pg_proc WHERE proname = 'get_experiment_traits'"
        )
        assert [r[0] for r in cur.fetchall()] == [5]
        cur.execute("SELECT pronargs FROM pg_proc WHERE proname = 'create_cyl_dataset'")
        assert [r[0] for r in cur.fetchall()] == [6]
        cur.execute("SELECT recipe_key FROM cyl_trait_sources WHERE id = %s", (src,))
        assert cur.fetchone()[0] == f"legacy:{src}"
