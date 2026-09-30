"""
Integration tests for add-cyl-trait-recipe-key, migration 1: the recipe-key helpers,
the recipe and run columns on cyl_trait_sources, and the backfill function.

Spec: cyl-trait-writeback "Trait source recipe and run columns", "Recipe key v1
definition", "Existing trait sources are backfilled with recipe identity" and
"Recipe-identity migrations are re-runnable and have exact rollbacks".

LOCAL ONLY: `pg_conn` connects as supabase_admin and every test rolls back.
"""

import json
import re
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from tests.integration.cyl_recipe_helpers import (  # noqa: E402
    RECIPE_AND_RUN_COLUMNS,
    apply_recipe_rollbacks,
    migration,
    recipe_columns,
    rollback,
    seed_scan,
    seed_source,
    sql_body,
)
from tests.integration.test_cyl_writeback_rpc import _call, _envelope  # noqa: E402

VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "recipe_key_v1_vectors.json").read_text(
        encoding="utf-8"
    )
)
PAYLOAD_FIELDS = {
    "predict_models",
    "predict_code_sha",
    "traits_code_sha",
    "predict_output_params",
}
# Mirrors the writeback spec's "does not depend on" list.
EXCLUDED_TOP_LEVEL = {
    "scan_key",
    "inputs",
    "params",
    "idempotency_key",
    "contract_version",
    "pipeline_run_id",
    "worker_request_id",
    "argo_workflow_uid",
    "argo_node_id",
    "produced_at",
    "traits_sleap_roots_version",
    "predict_container_digest",
    "traits_container_digest",
    "predict_inference_config",
}
EXCLUDED_MODEL_FIELDS = {"root_type", "sleap_nn_version"}
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _key(cur, text):
    cur.execute("SELECT public.cyl_trait_recipe_key_v1(%s::jsonb)", (text,))
    return cur.fetchone()[0]


def _payload(cur, text):
    cur.execute("SELECT public.cyl_trait_recipe_payload_v1(%s::jsonb)::text", (text,))
    return cur.fetchone()[0]


def _base():
    return json.loads(
        next(v for v in VECTORS["vectors"] if v["name"] == "base")["raw_provenance"]
    )


def _backfill(conn):
    notices = []
    conn.add_notice_handler(lambda d: notices.append(d.message_primary))
    with conn.cursor() as cur:
        cur.execute("SELECT public.cyl_backfill_trait_source_recipe_identity()")
    return notices


# --------------------------------------------------------------------------- #
# 2.2 Helpers
# --------------------------------------------------------------------------- #


def test_excluded_fields_match_contracts():
    top = set(VECTORS["provenance_fields"]) - PAYLOAD_FIELDS
    assert top == EXCLUDED_TOP_LEVEL
    assert set(VECTORS["model_ref_fields"]) - {
        "registry_id",
        "version",
        "weights_checksum",
    } == (EXCLUDED_MODEL_FIELDS)


@pytest.mark.parametrize("field", sorted(EXCLUDED_TOP_LEVEL))
def test_key_ignores_fields_outside_payload(pg_conn, field):
    base = _base()
    changed = dict(base)
    changed[field] = {"changed": field}
    with pg_conn.cursor() as cur:
        assert _key(cur, json.dumps(base)) == _key(cur, json.dumps(changed))


@pytest.mark.parametrize("field", sorted(EXCLUDED_MODEL_FIELDS))
def test_key_ignores_model_fields_outside_triple(pg_conn, field):
    base = _base()
    changed = json.loads(json.dumps(base))
    changed["predict_models"][0][field] = "changed"
    with pg_conn.cursor() as cur:
        assert _key(cur, json.dumps(base)) == _key(cur, json.dumps(changed))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p["predict_models"][0].update(registry_id="other"),
        lambda p: p["predict_models"][0].update(version="v9"),
        lambda p: p["predict_models"][0].update(weights_checksum="ffff"),
        lambda p: p.update(predict_code_sha="0000000"),
        lambda p: p.update(traits_code_sha="0000000"),
        lambda p: p.update(predict_output_params={"peak_threshold": 0.9}),
    ],
    ids=[
        "registry_id",
        "version",
        "weights_checksum",
        "predict_sha",
        "traits_sha",
        "output_params",
    ],
)
def test_key_changes_with_payload_inputs(pg_conn, mutate):
    base = _base()
    changed = json.loads(json.dumps(base))
    mutate(changed)
    with pg_conn.cursor() as cur:
        assert _key(cur, json.dumps(base)) != _key(cur, json.dumps(changed))


def test_checksum_null_differs_from_empty(pg_conn):
    base = _base()
    a, b = json.loads(json.dumps(base)), json.loads(json.dumps(base))
    a["predict_models"][0]["weights_checksum"] = None
    b["predict_models"][0]["weights_checksum"] = ""
    with pg_conn.cursor() as cur:
        assert _key(cur, json.dumps(a)) != _key(cur, json.dumps(b))


def test_model_order_and_empty_output_params(pg_conn):
    base = _base()
    reordered = json.loads(json.dumps(base))
    reordered["predict_models"].reverse()
    null_p, empty_p, absent_p = (json.loads(json.dumps(base)) for _ in range(3))
    null_p["predict_output_params"] = None
    empty_p["predict_output_params"] = {}
    absent_p.pop("predict_output_params")
    with pg_conn.cursor() as cur:
        assert _key(cur, json.dumps(base)) == _key(cur, json.dumps(reordered))
        keys = {_key(cur, json.dumps(p)) for p in (null_p, empty_p, absent_p)}
        assert len(keys) == 1


def test_duplicate_triples_counted_twice(pg_conn):
    base = _base()
    once = json.loads(json.dumps(base))
    once["predict_models"] = [base["predict_models"][0]]
    twice = json.loads(json.dumps(base))
    twice["predict_models"] = [
        base["predict_models"][0],
        dict(base["predict_models"][0], root_type="lateral"),
    ]
    with pg_conn.cursor() as cur:
        assert _key(cur, json.dumps(once)) != _key(cur, json.dumps(twice))


@pytest.mark.parametrize(
    "text",
    [
        "{}",
        '{"predict_code_sha": "a"}',
        '{"predict_models": []}',
        '{"predict_models": {"a": 1}}',
        '{"predict_models": "s"}',
        '{"predict_models": [1, "x", null]}',
        '{"predict_models": [{"registry_id": "r", "version": "v"}]}',
        '{"predict_output_params": "s"}',
        '{"predict_output_params": [1]}',
        '{"predict_code_sha": 7, "traits_code_sha": {"x": 1}}',
    ],
)
def test_odd_object_shapes_never_raise(pg_conn, text):
    with pg_conn.cursor() as cur:
        assert HEX64.match(_key(cur, text))
        assert _payload(cur, text) is not None


@pytest.mark.parametrize("text", [None, "[]", '"x"', "5", "null"])
def test_non_object_input_gives_null(pg_conn, text):
    with pg_conn.cursor() as cur:
        assert _key(cur, text) is None
        assert _payload(cur, text) is None


@pytest.mark.parametrize("vector", VECTORS["vectors"], ids=lambda v: v["name"])
def test_definition_hashes_to_key(pg_conn, vector):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT encode(sha256(convert_to(public.cyl_trait_recipe_payload_v1(%s::jsonb)::text,"
            " 'UTF8')), 'hex') = public.cyl_trait_recipe_key_v1(%s::jsonb)",
            (vector["raw_provenance"], vector["raw_provenance"]),
        )
        assert cur.fetchone()[0] is True


def test_partitions_match_contracts_identity(pg_conn):
    vectors = VECTORS["vectors"]
    with pg_conn.cursor() as cur:
        keys = [_key(cur, v["raw_provenance"]) for v in vectors]
        cur.execute("SELECT '{\"peak_threshold\": 1.0}'::jsonb::text")
        assert "1.0" in cur.fetchone()[0]
    for i, a in enumerate(vectors):
        for j, b in enumerate(vectors):
            if i >= j:
                continue
            same_partition = a["partition"] == b["partition"]
            diverges = (
                a["divergence_group"] is not None
                and a["divergence_group"] == b["divergence_group"]
            )
            expected_equal = same_partition and not diverges
            assert (keys[i] == keys[j]) is expected_equal, (a["name"], b["name"])


def test_helpers_immutable_owned_and_granted(pg_conn):
    with pg_conn.cursor() as cur:
        for fn in (
            "cyl_trait_recipe_payload_v1(jsonb)",
            "cyl_trait_recipe_key_v1(jsonb)",
        ):
            cur.execute(
                "SELECT provolatile, proowner::regrole::text FROM pg_proc WHERE oid = %s::regprocedure",
                (f"public.{fn}",),
            )
            assert cur.fetchone() == ("i", "postgres")
            cur.execute(
                "SELECT has_function_privilege('anon', %s, 'EXECUTE')",
                (f"public.{fn}",),
            )
            assert cur.fetchone()[0] is False
            for role in ("bloom_agent", "bloom_user", "bloom_admin", "authenticated"):
                cur.execute(
                    "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                    (role, f"public.{fn}"),
                )
                assert cur.fetchone()[0] is True, (role, fn)
        cur.execute("SET LOCAL ROLE bloom_agent")
        assert HEX64.match(_key(cur, json.dumps(_base())))


# --------------------------------------------------------------------------- #
# 2.3 Schema
# --------------------------------------------------------------------------- #


def test_columns_types_and_fks(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'cyl_trait_sources' "
            "AND column_name = ANY(%s)",
            (list(RECIPE_AND_RUN_COLUMNS),),
        )
        cols = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        assert cols == {
            "recipe_key": ("text", "YES"),
            "recipe_key_version": ("smallint", "YES"),
            "scan_id": ("bigint", "YES"),
            "argo_workflow_name": ("text", "YES"),
            "cyl_pipeline_run_id": ("bigint", "YES"),
        }
        cur.execute(
            "SELECT conname, confrelid::regclass::text, confdeltype FROM pg_constraint "
            "WHERE conrelid = 'public.cyl_trait_sources'::regclass AND contype = 'f' "
            "AND conname = ANY(%s)",
            (
                [
                    "cyl_trait_sources_scan_id_fkey",
                    "cyl_trait_sources_cyl_pipeline_run_id_fkey",
                ],
            ),
        )
        assert set(cur.fetchall()) == {
            ("cyl_trait_sources_scan_id_fkey", "cyl_scans", "n"),
            ("cyl_trait_sources_cyl_pipeline_run_id_fkey", "cyl_pipeline_runs", "n"),
        }


@pytest.mark.parametrize(
    "value, version, ok",
    [
        ("unattributed", 1, False),
        ("legacy:", 1, False),
        ("legacy:-1", 1, False),
        ("A" * 64, 1, False),
        ("a" * 63, 1, False),
        ("a" * 64 + "\n", 1, False),
        ("a" * 64, 1, True),
        ("legacy:12", 1, True),
        ("a" * 64, 2, False),
    ],
)
def test_recipe_key_checks(pg_conn, value, version, ok):
    with pg_conn.cursor() as cur:
        sid = seed_source(cur, None)
        cur.execute("SAVEPOINT chk")
        try:
            cur.execute(
                "UPDATE cyl_trait_sources SET recipe_key = %s, recipe_key_version = %s WHERE id = %s",
                (value, version, sid),
            )
            cur.execute("RELEASE SAVEPOINT chk")
            assert ok, value
        except psycopg.errors.CheckViolation:
            cur.execute("ROLLBACK TO SAVEPOINT chk")
            assert not ok, value


def test_indexes_exist(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' AND indexname = ANY(%s)",
            (
                [
                    "cyl_trait_sources_recipe_key_idx",
                    "cyl_trait_sources_scan_id_idx",
                    "cyl_pipeline_run_scans_argo_workflow_name_idx",
                ],
            ),
        )
        assert len(cur.fetchall()) == 3


@pytest.mark.parametrize("column", RECIPE_AND_RUN_COLUMNS)
def test_bloom_workflows_cannot_select_new_columns(pg_conn, column):
    with pg_conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE bloom_workflows")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(f"SELECT {column} FROM cyl_trait_sources LIMIT 1")


def test_deleting_scan_or_run_nulls_the_source_link(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, _ = seed_scan(cur, n_images=0)
        cur.execute(
            "INSERT INTO cyl_pipeline_runs (target_level, target_id, params, requested_by) "
            "VALUES ('scan', %s, '{}'::jsonb, '00000000-0000-0000-0000-000000000001') RETURNING id",
            (scan_id,),
        )
        run_id = cur.fetchone()[0]
        sid = seed_source(cur, None)
        cur.execute(
            "UPDATE cyl_trait_sources SET scan_id = %s, cyl_pipeline_run_id = %s WHERE id = %s",
            (scan_id, run_id, sid),
        )
        cur.execute("DELETE FROM cyl_scans WHERE id = %s", (scan_id,))
        cur.execute("DELETE FROM cyl_pipeline_runs WHERE id = %s", (run_id,))
        cols = recipe_columns(cur, sid)
        assert cols["scan_id"] is None and cols["cyl_pipeline_run_id"] is None


# --------------------------------------------------------------------------- #
# 2.4 Backfill
# --------------------------------------------------------------------------- #


def _meta(image_ids):
    base = _base()
    base["inputs"] = {"image_ids": image_ids, "images_checksum": "sha256:x"}
    return json.dumps(base)


def test_backfill_pipeline_source(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs = seed_scan(cur)
        meta = _meta([str(i) for i in imgs])
        sid = seed_source(cur, meta)
        assert recipe_columns(cur, sid)["recipe_key"] is None
    _backfill(pg_conn)
    with pg_conn.cursor() as cur:
        cols = recipe_columns(cur, sid)
        assert cols["recipe_key"] == _key(cur, meta)
        assert cols["recipe_key_version"] == 1
        assert cols["scan_id"] == scan_id


@pytest.mark.parametrize("meta", [None, "[]", "null"])
def test_backfill_legacy_and_non_object_metadata(pg_conn, meta):
    with pg_conn.cursor() as cur:
        sid = seed_source(cur, meta)
    _backfill(pg_conn)
    with pg_conn.cursor() as cur:
        cols = recipe_columns(cur, sid)
        assert cols["recipe_key"] == f"legacy:{sid}"
        assert cols["recipe_key_version"] == 1
        assert cols["scan_id"] is None


def _unresolved_count(cur):
    cur.execute(
        "SELECT count(*) FROM cyl_trait_sources "
        "WHERE jsonb_typeof(metadata) = 'object' AND scan_id IS NULL"
    )
    return cur.fetchone()[0]


def test_backfill_unresolvable_image_ids(pg_conn):
    with pg_conn.cursor() as cur:
        s1, imgs1 = seed_scan(cur, 1)
        s2, imgs2 = seed_scan(cur, 1)
        baseline = _unresolved_count(cur)
        bad = []
        for image_ids in (
            "MISSING",
            "not-array",
            [],
            ["abc"],
            ["999999999999"],
            [str(imgs1[0]), str(imgs2[0])],
            ["1" * 30],
        ):
            base = _base()
            if image_ids == "MISSING":
                base["inputs"] = {"images_checksum": "x"}
            elif image_ids == "not-array":
                base["inputs"] = {"image_ids": "7", "images_checksum": "x"}
            else:
                base["inputs"] = {"image_ids": image_ids, "images_checksum": "x"}
            bad.append(seed_source(cur, json.dumps(base)))
        dup = seed_source(cur, _meta([str(imgs1[0]), str(imgs1[0])]))
    notices = _backfill(pg_conn)
    with pg_conn.cursor() as cur:
        for sid in bad:
            assert recipe_columns(cur, sid)["scan_id"] is None
            assert HEX64.match(recipe_columns(cur, sid)["recipe_key"])
        assert recipe_columns(cur, dup)["scan_id"] == s1
    counts = [
        int(m.group(1))
        for n in notices
        if (
            m := re.match(
                r"cyl recipe backfill: (\d+) source\(s\) with unresolved image_ids", n
            )
        )
    ]
    assert counts == [baseline + len(bad)]


def test_backfill_never_sets_run_stamps(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = seed_scan(cur)
        sid = seed_source(cur, _meta([str(i) for i in imgs]))
    _backfill(pg_conn)
    with pg_conn.cursor() as cur:
        cols = recipe_columns(cur, sid)
        assert (
            cols["argo_workflow_name"] is None and cols["cyl_pipeline_run_id"] is None
        )


def test_backfill_agrees_with_rpc_written_trait_rows(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs = seed_scan(cur)
        res = _call(
            cur,
            _envelope(
                imgs,
                idempotency_key="recipe-bf-1",
                traits=[{"name": "t1", "scan_key": "SK1", "value": 1.0}],
            ),
        )
        sid = res["source_id"]
        cur.execute(
            "UPDATE cyl_trait_sources SET recipe_key = NULL, recipe_key_version = NULL, "
            "scan_id = NULL WHERE id = %s",
            (sid,),
        )
    _backfill(pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT scan_id FROM cyl_scan_traits WHERE source_id = %s", (sid,)
        )
        assert (
            [r[0] for r in cur.fetchall()]
            == [recipe_columns(cur, sid)["scan_id"]]
            == [scan_id]
        )


def test_backfill_is_rerunnable(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = seed_scan(cur)
        ids = [seed_source(cur, _meta([str(i) for i in imgs])), seed_source(cur, None)]
    _backfill(pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT to_jsonb(s) FROM cyl_trait_sources s WHERE id = ANY(%s) ORDER BY id",
            (ids,),
        )
        first = cur.fetchall()
    _backfill(pg_conn)
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT to_jsonb(s) FROM cyl_trait_sources s WHERE id = ANY(%s) ORDER BY id",
            (ids,),
        )
        assert cur.fetchall() == first


def test_backfill_owner_and_grants(pg_conn):
    fn = "public.cyl_backfill_trait_source_recipe_identity()"
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT proowner::regrole::text FROM pg_proc WHERE oid = %s::regprocedure",
            (fn,),
        )
        assert cur.fetchone()[0] == "postgres"
        for role in (
            "anon",
            "authenticated",
            "service_role",
            "bloom_agent",
            "bloom_user",
            "bloom_admin",
            "bloom_writer",
            "bloom_workflows",
        ):
            cur.execute("SELECT rolsuper FROM pg_roles WHERE rolname = %s", (role,))
            if cur.fetchone()[0]:
                continue
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, fn))
            assert cur.fetchone()[0] is False, role


# --------------------------------------------------------------------------- #
# 2.5 Migration and rollback
# --------------------------------------------------------------------------- #


def _overload_arg_counts(cur, name):
    cur.execute(
        "SELECT array_agg(pronargs ORDER BY pronargs) FROM pg_proc p "
        "JOIN pg_namespace n ON n.oid = p.pronamespace "
        "WHERE n.nspname = 'public' AND p.proname = %s",
        (name,),
    )
    return cur.fetchone()[0]


def test_migration_1_body_is_idempotent(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = seed_scan(cur)
        ids = [seed_source(cur, _meta([str(i) for i in imgs])), seed_source(cur, None)]
        cur.execute(sql_body(migration(1)))
        cur.execute(
            "SELECT to_jsonb(s) FROM cyl_trait_sources s WHERE id = ANY(%s) ORDER BY id",
            (ids,),
        )
        first = cur.fetchall()
        cur.execute(sql_body(migration(1)))
        cur.execute(
            "SELECT to_jsonb(s) FROM cyl_trait_sources s WHERE id = ANY(%s) ORDER BY id",
            (ids,),
        )
        assert cur.fetchall() == first
        assert _overload_arg_counts(cur, "insert_cyl_result_envelope") == [2]


def test_rollback_1_drops_exactly_its_objects(pg_conn):
    with pg_conn.cursor() as cur:
        apply_recipe_rollbacks(cur, down_to=1)
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = 'cyl_trait_sources' AND column_name = ANY(%s)",
            (list(RECIPE_AND_RUN_COLUMNS),),
        )
        assert cur.fetchall() == []
        cur.execute(
            "SELECT count(*) FROM pg_constraint WHERE conname LIKE 'cyl_trait_sources_%%' "
            "AND conname = ANY(%s)",
            (
                [
                    "cyl_trait_sources_scan_id_fkey",
                    "cyl_trait_sources_cyl_pipeline_run_id_fkey",
                    "cyl_trait_sources_recipe_key_format_check",
                    "cyl_trait_sources_recipe_key_version_check",
                ],
            ),
        )
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT count(*) FROM pg_indexes WHERE indexname = ANY(%s)",
            (
                [
                    "cyl_trait_sources_recipe_key_idx",
                    "cyl_trait_sources_scan_id_idx",
                    "cyl_pipeline_run_scans_argo_workflow_name_idx",
                ],
            ),
        )
        assert cur.fetchone()[0] == 0
        for fn in (
            "cyl_trait_recipe_payload_v1",
            "cyl_trait_recipe_key_v1",
            "cyl_backfill_trait_source_recipe_identity",
        ):
            assert _overload_arg_counts(cur, fn) is None, fn
        _, imgs = seed_scan(cur)
        res = _call(cur, _envelope(imgs, idempotency_key="recipe-rb1"))
        assert res["was_noop"] is False


def test_rollback_1_guard_detects_a_referencing_function(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "CREATE FUNCTION public._r1_probe() RETURNS bigint LANGUAGE plpgsql AS "
            "$$ BEGIN RETURN (SELECT count(recipe_key) FROM public.cyl_trait_sources); END $$"
        )
        cur.execute("SAVEPOINT guard")
        with pytest.raises(psycopg.errors.RaiseException, match="_r1_probe"):
            cur.execute(sql_body(rollback(1)))
        cur.execute("ROLLBACK TO SAVEPOINT guard")
        assert _overload_arg_counts(cur, "cyl_trait_recipe_key_v1") == [1]
