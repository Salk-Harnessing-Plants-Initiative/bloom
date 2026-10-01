"""
Integration tests for add-cyl-trait-recipe-key, migration 3: recipe-aware reads.

Spec: cyl-trait-read "Recipe presence is defined by trait rows", "Recipe listing for
a scan selection", "Per-scan recipe coverage", the MODIFIED "Bulk experiment-scoped
trait reads", "Recipe read functions are not executable by anon" and "Recipe read
migration replaces get_experiment_traits without leaving an overload".

The fixture seeds sources directly (as supabase_admin) with fixed, collision-proof ids
so the expected results below are exact. LOCAL ONLY: every test rolls back.
"""

import json
import os
import random
import urllib.error

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.cyl_recipe_helpers import (  # noqa: E402
    REPO_ROOT,
    acl_set,
    apply_recipe_rollbacks,
    migration,
    sql_body,
)
from tests.integration.test_cyl_read_path import (  # noqa: E402
    _register_trait,
    _seed_experiment,
    _seed_scan_in,
)

GET_COLUMNS = [
    "scan_id",
    "date_scanned",
    "plant_age_days",
    "wave_number",
    "plant_id",
    "germ_day",
    "plant_qr_code",
    "accession_name",
    "trait_name",
    "source_id",
    "trait_value",
    "recipe_key",
]
READ_ROLES = ("bloom_agent", "bloom_user", "bloom_admin")
OLD_BODY = (
    REPO_ROOT / "supabase" / "migrations" / "20260728000000_get_experiment_traits.sql"
)


class Fixture:
    """Experiments E1-E4 (see tasks.md 4.1). `s[name]` is a scan id, `src[n]` the id of
    fixture source n, `K[name]` a recipe key."""

    def __init__(self, cur):
        self.cur = cur
        self.base = random.randrange(10**9, 9 * 10**9, 1000)
        self.s, self.src, self.K = {}, {}, {}
        self.trait = {t: _register_trait(cur, f"recipe-fixture-{t}") for t in "ABC"}
        self.E1, w1 = _seed_experiment(cur)
        self.E2, w2 = _seed_experiment(cur)
        self.E3, w3 = _seed_experiment(cur)
        self.E4, w4 = _seed_experiment(cur)
        for name in (
            "a",
            "b",
            "c",
            "d",
            "e",
            "f",
            "g",
            "h",
            "i",
            "j",
            "j2",
            "k",
            "m",
            "n",
        ):
            self.s[name] = _seed_scan_in(cur, w1, n_images=0)[0]
        self.s["e2"] = _seed_scan_in(cur, w2, n_images=0)[0]
        self.s["e2b"] = _seed_scan_in(cur, w2, n_images=0)[0]
        self.s["e3L"] = _seed_scan_in(cur, w3, n_images=0)[0]
        self.s["e3N"] = _seed_scan_in(cur, w3, n_images=0)[0]
        self.s["e4"] = _seed_scan_in(cur, w4, n_images=0)[0]

        s = self.s
        L = self._source(5, None, scan=None)
        self.L = L
        self._source(10, "k1", scan="a", rows={"A": 1.0, "B": 2.0})
        self._source(50, "k1", scan="a", rows={"A": 5.0, "C": None})
        self._source(11, "k1", scan="b", rows={"A": 11.0})
        self._source(12, "k1", scan="c", rows={"A": 12.0})
        self._source(30, "k2", scan="d", rows={"A": 30.0})
        self._source(13, "k1", scan="e", rows={"A": 13.0})
        self._source(45, "k1", scan="e", rows={"A": 45.0})
        self._source(31, "k2", scan="e", rows={"A": 31.0})
        self._source(14, "k1", scan="f", rows={"A": 14.0})
        self._source(32, "k2", scan="f", rows={"A": 32.0})
        self._rows(None, s["f"], {"B": 0.5})
        self._rows(L, s["g"], {"A": 7.0})
        self._rows(None, s["h"], {"A": 8.0})
        self._source(40, "k3", scan="j")
        self._source(15, "k1", scan="j2", rows={"A": 15.0})
        self._source(41, "k3", scan="j2")
        self._source(16, "k1", scan=None, rows_on="k", rows={"A": 16.0})
        self._source(17, "k1", scan="m", rows={"A": 17.0}, recipe_key_null=True)
        self._source(22, "k1", scan="b", rows_on="n", rows={"A": 22.0})
        self._source(60, "k1", scan="e2", rows={"A": 60.0})
        self._source(70, "k4", scan="e2b", rows={"A": 70.0})
        self._rows(L, s["e3L"], {"A": 3.0})
        self._rows(None, s["e3N"], {"A": 3.5})
        self._rows(None, s["e4"], {"A": 4.0})
        self.K["legacy:L"] = f"legacy:{L}"

    def _meta(self, tag):
        return json.dumps(
            {
                "predict_models": [
                    {"registry_id": "r", "version": "v0", "weights_checksum": tag}
                ],
                "predict_code_sha": "p",
                "traits_code_sha": tag,
            }
        )

    def _source(self, n, tag, *, scan, rows=None, rows_on=None, recipe_key_null=False):
        sid = self.base + n
        meta = None if tag is None else self._meta(tag)
        scan_id = self.s[scan] if scan else None
        self.cur.execute(
            "INSERT INTO cyl_trait_sources (id, name, metadata, recipe_key, recipe_key_version, scan_id)"
            " VALUES (%s, %s, %s::jsonb, "
            "  CASE WHEN %s THEN NULL WHEN %s::jsonb IS NULL THEN 'legacy:' || %s"
            "       ELSE public.cyl_trait_recipe_key_v1(%s::jsonb) END, 1, %s)",
            (sid, f"fixture-{n}", meta, recipe_key_null, meta, sid, meta, scan_id),
        )
        self.src[n] = sid
        if tag and not recipe_key_null:
            self.cur.execute(
                "SELECT recipe_key FROM cyl_trait_sources WHERE id = %s", (sid,)
            )
            self.K[tag.upper()] = self.cur.fetchone()[0]
        if rows:
            self._rows(sid, self.s[rows_on or scan], rows)
        return sid

    def _rows(self, source_id, scan_id, rows):
        for t, v in rows.items():
            self.cur.execute(
                "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) VALUES (%s, %s, %s, %s)",
                (scan_id, source_id, self.trait[t], v),
            )


@pytest.fixture
def fx(pg_conn):
    with pg_conn.cursor() as cur:
        yield Fixture(cur)


def _list(cur, experiment_ids=None, scan_ids=None):
    cur.execute(
        "SELECT recipe_key, recipe_key_version, recipe_kind, definition, n_scans, "
        "newest_source_id, is_default FROM list_trait_recipes(%s, %s)",
        (experiment_ids, scan_ids),
    )
    cols = [d.name for d in cur.description]
    return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}


def _coverage(cur, experiment_ids=None, scan_ids=None, recipe_key=None):
    cur.execute(
        "SELECT scan_id, experiment_id, plant_qr_code, recipe_key, status, source_id, "
        "available_recipes FROM get_trait_recipe_coverage(%s, %s, %s)",
        (experiment_ids, scan_ids, recipe_key),
    )
    cols = [d.name for d in cur.description]
    return {r[0]: dict(zip(cols, r)) for r in cur.fetchall()}


def _traits(cur, exp, *, source_id=None, run_id=None, recipe_key=None, scan_ids=None):
    cur.execute(
        "SELECT * FROM get_experiment_traits(%s, %s, %s, %s, %s)",
        (exp, source_id, run_id, recipe_key, scan_ids),
    )
    cols = [d.name for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# --------------------------------------------------------------------------- #
# 4.2 list_trait_recipes
# --------------------------------------------------------------------------- #


def test_list_e1_counts_and_default(fx):
    got = _list(fx.cur, [fx.E1])
    K = fx.K
    assert {k: v["n_scans"] for k, v in got.items()} == {
        K["K1"]: 7,
        K["K2"]: 3,
        K["legacy:L"]: 1,
        "unattributed": 2,
    }
    assert K["K3"] not in got
    assert [k for k, v in got.items() if v["is_default"]] == [K["K1"]]
    assert got[K["K1"]]["newest_source_id"] == fx.src[50]
    assert got[K["K1"]]["recipe_kind"] == "pipeline"
    assert got[K["legacy:L"]]["recipe_kind"] == "legacy"
    assert got[K["legacy:L"]]["definition"] == {
        "source_id": fx.L,
        "source_name": "fixture-5",
    }
    u = got["unattributed"]
    assert (
        u["recipe_kind"],
        u["definition"],
        u["newest_source_id"],
        u["recipe_key_version"],
    ) == ("unattributed", None, None, None)
    assert got[K["K1"]]["recipe_key_version"] == 1


def test_list_pipeline_definition_hashes_to_key(fx):
    for key, row in _list(fx.cur, [fx.E1, fx.E2]).items():
        if row["recipe_kind"] != "pipeline":
            continue
        fx.cur.execute(
            "SELECT encode(sha256(convert_to(%s::jsonb::text, 'UTF8')), 'hex')",
            (json.dumps(row["definition"]),),
        )
        assert fx.cur.fetchone()[0] == key


def test_list_multi_experiment_default(fx):
    got = _list(fx.cur, [fx.E1, fx.E2])
    assert [k for k, v in got.items() if v["is_default"]] == [fx.K["K4"]]
    assert got[fx.K["K1"]]["n_scans"] == 8


def test_list_intersection_and_scan_selection(fx):
    got = _list(fx.cur, [fx.E1], [fx.s["a"], fx.s["e2"]])
    assert {k: v["n_scans"] for k, v in got.items()} == {fx.K["K1"]: 1}
    got = _list(fx.cur, None, [fx.s["e"]])
    assert {k: v["n_scans"] for k, v in got.items()} == {fx.K["K1"]: 1, fx.K["K2"]: 1}
    assert _list(fx.cur, None, [fx.s["i"]]) == {}


@pytest.mark.parametrize("args", [([], None), (None, []), ([1], [])])
def test_list_empty_arrays_select_nothing(fx, args):
    assert _list(fx.cur, *args) == {}


def test_list_requires_a_selection(fx):
    with pytest.raises(psycopg.errors.RaiseException, match="give experiment_ids_"):
        _list(fx.cur)


def test_presence_helper_selects_nothing_without_a_selection(fx):
    # The helper is reachable over PostgREST; with no selection it must not walk every
    # scan in the database (cyl-trait-read "Recipe presence is defined by trait rows").
    fx.cur.execute("SELECT count(*) FROM public._cyl_trait_recipe_presence(NULL, NULL)")
    assert fx.cur.fetchone()[0] == 0
    fx.cur.execute(
        "SELECT count(*) FROM public._cyl_trait_recipe_presence(%s, NULL)", ([fx.E1],)
    )
    assert fx.cur.fetchone()[0] > 0


def test_list_legacy_and_unattributed_defaults(fx):
    got = _list(fx.cur, [fx.E3])
    assert set(got) == {fx.K["legacy:L"], "unattributed"}
    assert got[fx.K["legacy:L"]]["is_default"] is True
    got = _list(fx.cur, [fx.E4])
    assert list(got) == ["unattributed"] and got["unattributed"]["is_default"] is True


# --------------------------------------------------------------------------- #
# 4.3 get_trait_recipe_coverage
# --------------------------------------------------------------------------- #


def test_coverage_e1_against_default(fx):
    got = _coverage(fx.cur, [fx.E1])
    s, src = fx.s, fx.src
    status = {name: got[s[name]]["status"] for name in s if s[name] in got}
    assert status == {
        "a": "included",
        "b": "included",
        "c": "included",
        "e": "included",
        "f": "included",
        "j2": "included",
        "k": "included",
        "d": "other_recipe",
        "m": "other_recipe",
        "n": "other_recipe",
        "g": "legacy_only",
        "h": "legacy_only",
        "i": "no_traits",
        "j": "no_traits",
    }
    assert got[s["a"]]["source_id"] == src[50]
    assert got[s["b"]]["source_id"] == src[11]
    assert got[s["e"]]["source_id"] == src[45]
    assert got[s["k"]]["source_id"] == src[16]
    assert all(r["recipe_key"] == fx.K["K1"] for r in got.values())
    for name in ("d", "g", "i", "m"):
        assert got[s[name]]["source_id"] is None
    assert got[s["f"]]["available_recipes"] == sorted(
        [fx.K["K1"], fx.K["K2"], "unattributed"]
    )
    assert (
        got[s["m"]]["available_recipes"] == []
        and got[s["n"]]["available_recipes"] == []
    )
    assert got[s["j"]]["available_recipes"] == []


def test_coverage_explicit_legacy_pick(fx):
    got = _coverage(fx.cur, [fx.E1], recipe_key=fx.K["legacy:L"])
    assert got[fx.s["g"]]["status"] == "included"
    assert got[fx.s["d"]]["status"] == "other_recipe"
    assert got[fx.s["h"]]["status"] == "legacy_only"


def test_coverage_no_trait_data(fx):
    got = _coverage(fx.cur, None, [fx.s["i"], fx.s["j"]])
    assert {r["status"] for r in got.values()} == {"no_traits"}
    assert {r["recipe_key"] for r in got.values()} == {None}


def test_coverage_unknown_key_raises_stored_key_accepted(fx):
    fx.cur.execute("SAVEPOINT unknown")
    with pytest.raises(psycopg.errors.RaiseException, match="unknown recipe_key"):
        _coverage(fx.cur, [fx.E1], recipe_key="f" * 64)
    fx.cur.execute("ROLLBACK TO SAVEPOINT unknown")
    got = _coverage(fx.cur, [fx.E1], recipe_key=fx.K["K3"])
    assert "included" not in {r["status"] for r in got.values()}


def test_coverage_one_scan(fx):
    got = _coverage(fx.cur, None, [fx.s["e"]])
    assert list(got) == [fx.s["e"]]


# --------------------------------------------------------------------------- #
# 4.4 get_experiment_traits recipe mode
# --------------------------------------------------------------------------- #


def test_recipe_read_one_recipe_and_highest_source(fx):
    rows = _traits(fx.cur, fx.E1, recipe_key=fx.K["K1"])
    assert {r["recipe_key"] for r in rows} == {fx.K["K1"]}
    by_scan = {}
    for r in rows:
        by_scan.setdefault(r["scan_id"], set()).add(r["source_id"])
    assert by_scan[fx.s["e"]] == {fx.src[45]}
    assert fx.s["d"] not in by_scan
    assert by_scan[fx.s["k"]] == {fx.src[16]}
    assert fx.s["n"] not in by_scan


def test_recipe_read_no_mixing_and_null_value(fx):
    rows = [
        r
        for r in _traits(fx.cur, fx.E1, recipe_key=fx.K["K1"])
        if r["scan_id"] == fx.s["a"]
    ]
    names = {r["trait_name"]: r["trait_value"] for r in rows}
    assert set(names) == {"recipe-fixture-A", "recipe-fixture-C"}
    assert names["recipe-fixture-A"] == 5.0 and names["recipe-fixture-C"] is None


def test_recipe_read_unattributed(fx):
    rows = _traits(fx.cur, fx.E1, recipe_key="unattributed")
    assert {r["scan_id"] for r in rows} == {fx.s["f"], fx.s["h"]}
    assert all(
        r["source_id"] is None and r["recipe_key"] == "unattributed" for r in rows
    )


def test_recipe_read_legacy_equals_source_pin(fx):
    by_key = _traits(fx.cur, fx.E1, recipe_key=fx.K["legacy:L"])
    by_src = _traits(fx.cur, fx.E1, source_id=fx.L)
    strip = [lambda r: {k: v for k, v in r.items() if k != "recipe_key"}]
    assert [strip[0](r) for r in by_key] == [strip[0](r) for r in by_src]


def test_recipe_read_stored_elsewhere_returns_nothing(fx):
    assert _traits(fx.cur, fx.E1, recipe_key=fx.K["K3"]) == []
    assert _traits(fx.cur, fx.E1, recipe_key=fx.K["K4"]) == []


@pytest.mark.parametrize("bad", ["f" * 64, "legacy:999999999999", "A" * 64, "foo"])
def test_recipe_read_mistyped_key_raises(fx, bad):
    with pytest.raises(psycopg.errors.RaiseException, match="unknown recipe_key"):
        _traits(fx.cur, fx.E1, recipe_key=bad)


@pytest.mark.parametrize(
    "kw",
    [
        {"source_id": 1, "run_id": "r"},
        {"source_id": 1, "recipe_key": "unattributed"},
        {"run_id": "r", "recipe_key": "unattributed"},
    ],
)
def test_two_selectors_raise(fx, kw):
    with pytest.raises(psycopg.errors.RaiseException, match="at most one of"):
        _traits(fx.cur, fx.E1, **kw)


@pytest.mark.parametrize("mode", ["default", "source", "recipe", "unattributed"])
def test_scan_ids_narrow_every_mode(fx, mode):
    kw = {
        "default": {},
        "source": {"source_id": fx.src[32]},
        "recipe": {"recipe_key": fx.K["K2"]},
        "unattributed": {"recipe_key": "unattributed"},
    }[mode]
    only_f = _traits(fx.cur, fx.E1, scan_ids=[fx.s["f"]], **kw)
    assert only_f and {r["scan_id"] for r in only_f} == {fx.s["f"]}
    assert _traits(fx.cur, fx.E1, scan_ids=[], **kw) == []
    assert _traits(fx.cur, fx.E1, scan_ids=[fx.s["e2"]], **kw) == []


def test_recipe_key_column_in_every_mode(fx):
    rows = _traits(fx.cur, fx.E1)
    for r in rows:
        if r["source_id"] is None:
            assert r["recipe_key"] == "unattributed"
        elif r["source_id"] == fx.src[17]:
            assert r["recipe_key"] is None
        else:
            assert r["recipe_key"] is not None


def test_result_columns_and_order(fx):
    for kw in ({}, {"recipe_key": fx.K["K1"]}, {"source_id": fx.L}):
        fx.cur.execute(
            "SELECT * FROM get_experiment_traits(%s, %s, %s, %s, %s)",
            (fx.E1, kw.get("source_id"), None, kw.get("recipe_key"), None),
        )
        assert [d.name for d in fx.cur.description] == GET_COLUMNS


def test_functions_agree_on_source_of_recipe(fx):
    cov = _coverage(fx.cur, [fx.E1], recipe_key=fx.K["K1"])
    rows = _traits(fx.cur, fx.E1, recipe_key=fx.K["K1"])
    for scan_id, c in cov.items():
        if c["status"] == "included":
            assert {r["source_id"] for r in rows if r["scan_id"] == scan_id} == {
                c["source_id"]
            }


# --------------------------------------------------------------------------- #
# 4.7 default-path parity with the 20260728000000 definition
# --------------------------------------------------------------------------- #


def test_default_path_parity(fx):
    cur = fx.cur
    old_modes = [
        (fx.E1, None, None),
        (fx.E1, fx.src[32], None),
        (fx.E1, None, "no-such-run"),
    ]
    apply_recipe_rollbacks(cur, down_to=3)
    before = []
    for args in old_modes:
        cur.execute("SELECT * FROM get_experiment_traits(%s, %s, %s)", args)
        before.append(cur.fetchall())
    cur.execute(sql_body(migration(3)))
    for args, old in zip(old_modes, before):
        cur.execute("SELECT * FROM get_experiment_traits(%s, %s, %s)", args)
        assert [r[:11] for r in cur.fetchall()] == old


# --------------------------------------------------------------------------- #
# 4.8 grants, 4.9 gateway
# --------------------------------------------------------------------------- #

_SIGS = (
    "public.get_experiment_traits(bigint,bigint,text,text,bigint[])",
    "public.list_trait_recipes(bigint[],bigint[])",
    "public.get_trait_recipe_coverage(bigint[],bigint[],text)",
)


@pytest.mark.parametrize("role", READ_ROLES)
def test_read_roles_can_call_every_mode(fx, role):
    cur = fx.cur
    cur.execute(f"SET LOCAL ROLE {role}")
    for kw in (
        {},
        {"recipe_key": fx.K["K1"]},
        {"recipe_key": fx.K["legacy:L"]},
        {"recipe_key": "unattributed"},
        {"scan_ids": [fx.s["f"]]},
    ):
        assert _traits(cur, fx.E1, **kw)
        assert _coverage(cur, [fx.E1], kw.get("scan_ids"), kw.get("recipe_key"))
    assert _list(cur, [fx.E1])
    cur.execute("RESET ROLE")


def test_grants_and_invoker(pg_conn):
    with pg_conn.cursor() as cur:
        for sig in _SIGS:
            cur.execute(
                "SELECT prosecdef FROM pg_proc WHERE oid = %s::regprocedure", (sig,)
            )
            assert cur.fetchone()[0] is False, sig
            for role, expected in (("authenticated", True), ("anon", False)):
                cur.execute(
                    "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig)
                )
                assert cur.fetchone()[0] is expected, (role, sig)


def _gateway(api, key, fn, body):
    try:
        return api(f"/api/rest/v1/rpc/{fn}", api_key=key, method="POST", data=body)
    except (urllib.error.URLError, OSError) as e:
        if os.environ.get("CI"):
            pytest.fail(f"PostgREST gateway not reachable in CI: {e}")
        pytest.skip(f"PostgREST gateway not reachable ({e}); CI covers this")


def test_bloommcp_three_key_call_resolves(api, service_role_key):
    status, body = _gateway(
        api,
        service_role_key,
        "get_experiment_traits",
        {"experiment_id_": 1, "source_id_": None, "run_id_": None},
    )
    assert status == 200 and isinstance(body, list), (status, body)


def test_list_trait_recipes_reachable(api, service_role_key):
    status, body = _gateway(
        api, service_role_key, "list_trait_recipes", {"experiment_ids_": [1]}
    )
    assert status == 200, (status, body)


def test_anon_cannot_call_get_experiment_traits(api, anon_key):
    status, body = _gateway(
        api, anon_key, "get_experiment_traits", {"experiment_id_": 1}
    )
    assert status in (401, 403), (status, body)


# --------------------------------------------------------------------------- #
# 4.10 migration and rollback
# --------------------------------------------------------------------------- #


def _arg_counts(cur, name):
    cur.execute(
        "SELECT array_agg(pronargs ORDER BY pronargs) FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname = %s",
        (name,),
    )
    return cur.fetchone()[0]


def test_migration_3_body_is_idempotent(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(sql_body(migration(3)))
        cur.execute(sql_body(migration(3)))
        assert _arg_counts(cur, "get_experiment_traits") == [5]


@pytest.mark.parametrize(
    "sig",
    [
        "public._cyl_trait_recipe_presence(bigint[],bigint[])",
        "public.list_trait_recipes(bigint[],bigint[])",
        "public.get_trait_recipe_coverage(bigint[],bigint[],text)",
        "public.get_experiment_traits(bigint,bigint,text,text,bigint[])",
    ],
)
def test_recipe_read_functions_pin_search_path(pg_conn, sig):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT proconfig FROM pg_proc WHERE oid = %s::regprocedure", (sig,)
        )
        expected = ["search_path=pg_catalog, public"]
        if sig.startswith("public.get_experiment_traits("):
            # 20261001180000; see test_get_experiment_traits_plan.py.
            expected += ["join_collapse_limit=12", "from_collapse_limit=12"]
        assert cur.fetchone()[0] == expected


def test_rollback_3_refuses_while_dataset_recipe_mode_is_live(pg_conn):
    # R3 drops _cyl_trait_recipe_presence; M4's create_cyl_dataset calls it, and plpgsql
    # does not track that dependency, so R3 must refuse until R4 has run.
    from tests.integration.cyl_recipe_helpers import rollback

    with pg_conn.cursor() as cur:
        cur.execute("SAVEPOINT r3")
        with pytest.raises(psycopg.errors.RaiseException, match="create_cyl_dataset"):
            cur.execute(sql_body(rollback(3)))
        cur.execute("ROLLBACK TO SAVEPOINT r3")
        cur.execute(sql_body(rollback(4)))
        cur.execute(sql_body(rollback(3)))
        assert _arg_counts(cur, "get_experiment_traits") == [3]


def _attrs(cur, sig):
    cur.execute(
        "SELECT prosecdef, provolatile, proconfig FROM pg_proc WHERE oid = %s::regprocedure",
        (sig,),
    )
    return cur.fetchone(), acl_set(cur, sig)


def test_rollback_3_restores_three_arg_function(pg_conn):
    old_sig = "public.get_experiment_traits(bigint,bigint,text)"
    with pg_conn.cursor() as cur:
        apply_recipe_rollbacks(cur, down_to=3)
        assert _arg_counts(cur, "get_experiment_traits") == [3]
        assert _arg_counts(cur, "list_trait_recipes") is None
        assert _arg_counts(cur, "get_trait_recipe_coverage") is None
        restored = _attrs(cur, old_sig)
        cur.execute(f"DROP FUNCTION {old_sig}")
        # As db push does: create as postgres, so owner and default grants match.
        cur.execute("SET LOCAL ROLE postgres")
        cur.execute(sql_body(OLD_BODY))
        cur.execute("RESET ROLE")
        assert _attrs(cur, old_sig) == restored
        cur.execute(sql_body(migration(3)))
        assert _arg_counts(cur, "get_experiment_traits") == [5]


def test_rollback_1_still_refuses_with_m3_live(pg_conn):
    from tests.integration.cyl_recipe_helpers import rollback

    with pg_conn.cursor() as cur:
        cur.execute(sql_body(rollback(2)))
        cur.execute("SAVEPOINT r1")
        with pytest.raises(
            psycopg.errors.RaiseException, match="get_experiment_traits"
        ):
            cur.execute(sql_body(rollback(1)))
        cur.execute("ROLLBACK TO SAVEPOINT r1")
