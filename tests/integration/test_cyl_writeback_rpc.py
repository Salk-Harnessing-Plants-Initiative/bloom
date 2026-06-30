"""
Integration tests for changes D + E (`add-cyl-writeback-rpc`).

Change D is `insert_cyl_result_envelope(jsonb)` — a SECURITY DEFINER RPC that
ingests one sleap-roots-contracts ResultEnvelope and writes it, in one
transaction, into `cyl_trait_sources` (provenance + idempotency anchor),
`cyl_scan_traits` (long-format values via the `cyl_traits` registry), and
`cyl_scan_intermediates` (per-scan blob pointers). Re-delivery of an already
ingested run is a pure no-op (the source insert is the atomic gate).

Change E locks the three tables to RPC-only writes: the legacy `authenticated`
INSERT policies and `bloom_writer`'s INSERT/UPDATE policies are dropped, so only
the RPC (via its `postgres` owner) and `bloom_admin` can write.

LOCAL ONLY: the `pg_conn` fixture connects to 127.0.0.1 on POSTGRES_HOST_PORT as
`supabase_admin` (BYPASSRLS) and mutates nothing — every test rolls back. RLS is
exercised with `SET LOCAL ROLE`. Runs in CI's `compose-health-check` job.
"""

import json
import re
from pathlib import Path

import pytest

# Skip the whole module if psycopg isn't available (matches the sibling tests).
psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
RPC = "public.insert_cyl_result_envelope"
PINNED_VERSION = "v0.1.0a2"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _seed_scan(cur, n_images: int = 2):
    """Seed one scan with `n_images` images (FK parents), as supabase_admin."""
    cur.execute("INSERT INTO cyl_scans DEFAULT VALUES RETURNING id")
    scan_id = cur.fetchone()[0]
    img_ids = []
    for _ in range(n_images):
        cur.execute("INSERT INTO cyl_images (scan_id) VALUES (%s) RETURNING id", (scan_id,))
        img_ids.append(cur.fetchone()[0])
    return scan_id, img_ids


def _trait(name, value, *, scan_key="SK1", grain=None):
    t = {"name": name, "scan_key": scan_key, "value": value}
    if grain is not None:
        t["grain"] = grain
    return t


def _blob(*, kind="predictions_slp", root_type="primary", scan_key="SK1",
          s3_location="s3://bloom/p.slp", box_link=None, checksum=None, file_size=None):
    return {
        "kind": kind, "root_type": root_type, "scan_key": scan_key,
        "s3_location": s3_location, "box_link": box_link,
        "checksum": checksum, "file_size": file_size,
    }


def _envelope(image_ids, *, contract_version=PINNED_VERSION, scan_key="SK1",
              idempotency_key="key-1", pipeline_run_id=None, traits=None, blobs=None,
              drop_provenance=False, drop_inputs=False):
    prov = {
        "contract_version": contract_version,
        "scan_key": scan_key,
        "idempotency_key": idempotency_key,
        "inputs": {"image_ids": [str(i) for i in image_ids]},
    }
    if pipeline_run_id is not None:
        prov["pipeline_run_id"] = pipeline_run_id
    if drop_inputs:
        prov.pop("inputs")
    env = {"provenance": prov, "traits": traits or [], "blobs": blobs or []}
    if drop_provenance:
        env.pop("provenance")
    return env


def _call(cur, envelope):
    """Call the RPC, returning the parsed jsonb summary."""
    cur.execute(f"SELECT {RPC}(%s::jsonb)", (json.dumps(envelope),))
    res = cur.fetchone()[0]
    return json.loads(res) if isinstance(res, str) else res


def _source_id(cur, idem):
    cur.execute("SELECT id FROM cyl_trait_sources WHERE idempotency_key = %s", (idem,))
    row = cur.fetchone()
    return row[0] if row else None


def _trait_rows(cur, idem):
    cur.execute(
        "SELECT t.name, st.value, st.scan_id FROM cyl_scan_traits st "
        "JOIN cyl_trait_sources s ON st.source_id = s.id "
        "JOIN cyl_traits t ON st.trait_id = t.id WHERE s.idempotency_key = %s",
        (idem,),
    )
    return cur.fetchall()


def _blob_count(cur, idem):
    cur.execute(
        "SELECT count(*) FROM cyl_scan_intermediates ci "
        "JOIN cyl_trait_sources s ON ci.source_id = s.id WHERE s.idempotency_key = %s",
        (idem,),
    )
    return cur.fetchone()[0]


# --------------------------------------------------------------------------- #
# 2.1 / 2.2 / 2.3 — happy path, source name, return shape
# --------------------------------------------------------------------------- #


def test_valid_envelope_writes_source_traits_blobs(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs = _seed_scan(cur, 2)
        env = _envelope(
            imgs, idempotency_key="happy",
            traits=[_trait("primary_root_length", 125.5), _trait("lateral_count", 12)],
            blobs=[_blob(root_type="primary"), _blob(root_type="lateral", s3_location="s3://b/l.slp")],
        )
        res = _call(cur, env)
        assert res["was_noop"] is False
        assert res["scan_id"] == scan_id
        assert res["trait_count"] == 2
        assert res["blob_count"] == 2
        # source row: name non-null, metadata = provenance, key set
        cur.execute(
            "SELECT name, metadata->>'scan_key', idempotency_key FROM cyl_trait_sources "
            "WHERE idempotency_key = 'happy'"
        )
        name, md_scan_key, key = cur.fetchone()
        assert name is not None and md_scan_key == "SK1" and key == "happy"
        # trait rows reference the resolved scan and a resolved trait_id
        rows = _trait_rows(cur, "happy")
        assert {r[0] for r in rows} == {"primary_root_length", "lateral_count"}
        assert all(r[2] == scan_id for r in rows)
        assert _blob_count(cur, "happy") == 2
    pg_conn.rollback()


def test_source_name_is_nonnull_and_deterministic(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        # pipeline_run_id present -> used as the label
        _call(cur, _envelope(imgs, idempotency_key="n1", pipeline_run_id="run-42"))
        cur.execute("SELECT name FROM cyl_trait_sources WHERE idempotency_key='n1'")
        assert cur.fetchone()[0] == "run-42"
        # absent pipeline_run_id -> deterministic key-derived label (full key)
        _, imgs2 = _seed_scan(cur)
        _call(cur, _envelope(imgs2, idempotency_key="n2"))
        cur.execute("SELECT name FROM cyl_trait_sources WHERE idempotency_key='n2'")
        assert cur.fetchone()[0] == "sleap-roots:n2"
    pg_conn.rollback()


def test_return_value_reports_noop_flag(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        env = _envelope(imgs, idempotency_key="ret", traits=[_trait("x", 1.0)])
        first = _call(cur, env)
        second = _call(cur, env)
        assert set(first) == {"source_id", "scan_id", "trait_count", "blob_count", "was_noop"}
        assert first["was_noop"] is False and second["was_noop"] is True
        assert second["source_id"] == first["source_id"]
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 2.4–2.7 — idempotency, immutability, pure no-op
# --------------------------------------------------------------------------- #


def test_redelivery_is_pure_noop(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        env = _envelope(
            imgs, idempotency_key="idem",
            traits=[_trait("a", 1.0)], blobs=[_blob()],
        )
        _call(cur, env)
        _call(cur, env)
        cur.execute("SELECT count(*) FROM cyl_trait_sources WHERE idempotency_key='idem'")
        assert cur.fetchone()[0] == 1
        assert len(_trait_rows(cur, "idem")) == 1
        assert _blob_count(cur, "idem") == 1
    pg_conn.rollback()


def test_redelivery_divergent_metadata_does_not_overwrite(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        _call(cur, _envelope(imgs, idempotency_key="im", pipeline_run_id="first"))
        # same key, different metadata (different pipeline_run_id)
        res = _call(cur, _envelope(imgs, idempotency_key="im", pipeline_run_id="second"))
        assert res["was_noop"] is True
        cur.execute("SELECT metadata->>'pipeline_run_id' FROM cyl_trait_sources WHERE idempotency_key='im'")
        assert cur.fetchone()[0] == "first"  # never overwritten
    pg_conn.rollback()


def test_key_metadata_invariant_holds_on_written_row(pg_conn):
    # The dedup-anchor column equals the value nested in the stored Provenance —
    # the RPC writes both from the same envelope field (invariant by construction).
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        _call(cur, _envelope(imgs, idempotency_key="inv"))
        cur.execute(
            "SELECT idempotency_key = metadata->>'idempotency_key' "
            "FROM cyl_trait_sources WHERE idempotency_key='inv'"
        )
        assert cur.fetchone()[0] is True
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 2.8–2.9 — contract version + idempotency-key validation
# --------------------------------------------------------------------------- #


def test_contract_version_mismatch_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        with pytest.raises(psycopg.errors.RaiseException):
            _call(cur, _envelope(imgs, contract_version="v0.0.0a0", idempotency_key="cv"))
    pg_conn.rollback()


def test_matching_contract_version_accepted(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        res = _call(cur, _envelope(imgs, idempotency_key="cvok"))
        assert res["was_noop"] is False
    pg_conn.rollback()


def test_empty_or_absent_idempotency_key_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        with pytest.raises(psycopg.errors.RaiseException):
            _call(cur, _envelope(imgs, idempotency_key=""))
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 2.10 — trait-name registry (auto-register), cross-delivery idempotency
# --------------------------------------------------------------------------- #


def test_unseen_trait_name_is_auto_registered(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        novel = "novel_trait_xyz"
        cur.execute("SELECT count(*) FROM cyl_traits WHERE name=%s", (novel,))
        assert cur.fetchone()[0] == 0
        _call(cur, _envelope(imgs, idempotency_key="reg", traits=[_trait(novel, 1.0)]))
        cur.execute("SELECT count(*) FROM cyl_traits WHERE name=%s", (novel,))
        assert cur.fetchone()[0] == 1
        rows = _trait_rows(cur, "reg")
        assert rows[0][0] == novel
    pg_conn.rollback()


def test_auto_register_idempotent_across_deliveries(pg_conn):
    with pg_conn.cursor() as cur:
        name = "shared_trait_abc"
        _, imgs1 = _seed_scan(cur)
        _call(cur, _envelope(imgs1, idempotency_key="d1", traits=[_trait(name, 1.0)]))
        _, imgs2 = _seed_scan(cur)
        _call(cur, _envelope(imgs2, idempotency_key="d2", traits=[_trait(name, 2.0)]))
        cur.execute("SELECT count(*) FROM cyl_traits WHERE name=%s", (name,))
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 2.11 — grain
# --------------------------------------------------------------------------- #


def test_image_grain_trait_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        with pytest.raises(psycopg.errors.RaiseException):
            _call(cur, _envelope(imgs, idempotency_key="g",
                                 traits=[_trait("t", 1.0, grain="image")]))
    pg_conn.rollback()


def test_omitted_grain_accepted_as_scan(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        res = _call(cur, _envelope(imgs, idempotency_key="g2", traits=[_trait("t", 1.0)]))
        assert res["trait_count"] == 1
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 2.12 — finite-or-null (post-cast)
# --------------------------------------------------------------------------- #


# JSON has no NaN/inf literal (a conforming producer already normalized numeric
# NaN/inf to null), so the reachable non-finite cases are JSON null, the non-
# conforming string forms "NaN"/"Infinity", and a finite number out of real range.
@pytest.mark.parametrize("bad_value", [None, "NaN", "Infinity", 1e40])
def test_non_finite_or_overflow_value_is_null(pg_conn, bad_value):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        env = _envelope(imgs, idempotency_key="fin", traits=[_trait("tt", bad_value)])
        _call(cur, env)
        rows = _trait_rows(cur, "fin")
        assert rows[0][1] is None, f"{bad_value!r} should normalize to NULL, got {rows[0][1]!r}"
    pg_conn.rollback()


def test_finite_value_round_trips(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        _call(cur, _envelope(imgs, idempotency_key="rt", traits=[_trait("tt", 42.5)]))
        rows = _trait_rows(cur, "rt")
        assert rows[0][1] == pytest.approx(42.5, rel=1e-6)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 2.13 — scan resolution
# --------------------------------------------------------------------------- #


def test_multi_image_one_scan_resolves(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs = _seed_scan(cur, 3)
        res = _call(cur, _envelope(imgs, idempotency_key="sr"))
        assert res["scan_id"] == scan_id
    pg_conn.rollback()


def test_cross_scan_image_ids_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs1 = _seed_scan(cur, 1)
        _, imgs2 = _seed_scan(cur, 1)
        with pytest.raises(psycopg.errors.RaiseException):
            _call(cur, _envelope(imgs1 + imgs2, idempotency_key="xs"))
    pg_conn.rollback()


@pytest.mark.parametrize("ids", [[], ["999999999"], ["not-a-number"]])
def test_unresolvable_image_ids_rejected(pg_conn, ids):
    with pg_conn.cursor() as cur:
        _seed_scan(cur)
        env = _envelope([1], idempotency_key="ur")
        env["provenance"]["inputs"]["image_ids"] = ids
        with pytest.raises(psycopg.errors.RaiseException):
            _call(cur, env)
    pg_conn.rollback()


def test_partial_match_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur, 1)
        env = _envelope(imgs, idempotency_key="pm")
        env["provenance"]["inputs"]["image_ids"] = [str(imgs[0]), "888888888"]
        with pytest.raises(psycopg.errors.RaiseException):
            _call(cur, env)
    pg_conn.rollback()


def test_duplicate_image_id_one_scan_accepted(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs = _seed_scan(cur, 1)
        env = _envelope(imgs, idempotency_key="dup")
        env["provenance"]["inputs"]["image_ids"] = [str(imgs[0]), str(imgs[0])]
        res = _call(cur, env)
        assert res["scan_id"] == scan_id
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 2.14 — envelope self-consistency / structure
# --------------------------------------------------------------------------- #


def test_scan_key_mismatch_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        with pytest.raises(psycopg.errors.RaiseException):
            _call(cur, _envelope(imgs, idempotency_key="sk",
                                 traits=[_trait("t", 1.0, scan_key="OTHER")]))
    pg_conn.rollback()


@pytest.mark.parametrize("mutate", ["drop_provenance", "drop_inputs", "array", "scalar"])
def test_malformed_envelope_rejected(pg_conn, mutate):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        if mutate == "array":
            env = []
        elif mutate == "scalar":
            env = 5
        else:
            env = _envelope(imgs, idempotency_key="mal", **{mutate: True})
        with pytest.raises(psycopg.errors.RaiseException):
            _call(cur, env)
    pg_conn.rollback()


def test_empty_traits_and_blobs_writes_only_source(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        res = _call(cur, _envelope(imgs, idempotency_key="empty", traits=[], blobs=[]))
        assert res["trait_count"] == 0 and res["blob_count"] == 0 and res["was_noop"] is False
        assert _source_id(cur, "empty") is not None
        assert len(_trait_rows(cur, "empty")) == 0
        assert _blob_count(cur, "empty") == 0
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 2.15 — all-or-nothing including the registry
# --------------------------------------------------------------------------- #


def test_all_or_nothing_rolls_back_registry(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        novel = "rollback_trait_qqq"
        env = _envelope(
            imgs, idempotency_key="aon",
            traits=[_trait(novel, 1.0)],
            blobs=[_blob(kind="not_a_valid_kind")],  # CHECK violation aborts the call
        )
        with pytest.raises(psycopg.errors.CheckViolation):
            _call(cur, env)
        cur.execute("ROLLBACK")  # the failed RPC aborted the txn; recover the connection
        cur.execute("BEGIN")
        assert _source_id(cur, "aon") is None
        cur.execute("SELECT count(*) FROM cyl_traits WHERE name=%s", (novel,))
        assert cur.fetchone()[0] == 0
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 2.16 — same key, different scan
# --------------------------------------------------------------------------- #


def test_same_key_different_scan_short_circuits(pg_conn):
    with pg_conn.cursor() as cur:
        s1, imgs1 = _seed_scan(cur, 1)
        s2, imgs2 = _seed_scan(cur, 1)
        _call(cur, _envelope(imgs1, idempotency_key="sk-coll", traits=[_trait("t", 1.0)]))
        res = _call(cur, _envelope(imgs2, idempotency_key="sk-coll", traits=[_trait("t", 2.0)]))
        assert res["was_noop"] is True
        cur.execute("SELECT count(*) FROM cyl_trait_sources WHERE idempotency_key='sk-coll'")
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT count(*) FROM cyl_scan_traits st "
            "JOIN cyl_trait_sources s ON st.source_id=s.id "
            "WHERE s.idempotency_key='sk-coll' AND st.scan_id=%s", (s2,),
        )
        assert cur.fetchone()[0] == 0  # nothing attached to the divergent scan
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 3.1 / 3.2 — SECURITY DEFINER hardening + EXECUTE grants
# --------------------------------------------------------------------------- #

TABLES = ["cyl_trait_sources", "cyl_scan_traits", "cyl_scan_intermediates"]


def test_function_is_hardened(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT prosecdef, proconfig, pg_get_userbyid(proowner) "
            "FROM pg_proc WHERE proname='insert_cyl_result_envelope'"
        )
        secdef, proconfig, owner = cur.fetchone()
        assert secdef is True
        assert any(c.startswith("search_path=") for c in proconfig)
        assert owner == "postgres"
        cur.execute("SELECT rolbypassrls FROM pg_roles WHERE rolname='postgres'")
        assert cur.fetchone()[0] is True, "owner must bypass RLS to write post-lockdown"
        for table in TABLES:
            cur.execute("SELECT relforcerowsecurity FROM pg_class WHERE oid=%s::regclass",
                        (f"public.{table}",))
            assert cur.fetchone()[0] is False, f"FORCE RLS on {table} would break the definer"
    pg_conn.rollback()


def test_execute_grants_are_exactly_the_sanctioned_roles(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_function_privilege('public', %s, 'EXECUTE')",
                    (f"{RPC}(jsonb)",))
        assert cur.fetchone()[0] is False, "PUBLIC must not execute the RPC"
        for role in ["bloom_writer", "service_role", "bloom_admin"]:
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                        (role, f"{RPC}(jsonb)"))
            assert cur.fetchone()[0] is True, f"{role} should hold EXECUTE"
        for role in ["bloom_user", "bloom_agent"]:
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                        (role, f"{RPC}(jsonb)"))
            assert cur.fetchone()[0] is False, f"{role} must not hold EXECUTE"
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# 3.3 / 3.4 — E lockdown: RPC is the sole writer
# --------------------------------------------------------------------------- #


def test_bloom_roles_are_not_bypassrls(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT rolname, rolbypassrls FROM pg_roles "
            "WHERE rolname IN ('bloom_user','bloom_writer','authenticated')"
        )
        bypass = {r: b for r, b in cur.fetchall()}
    assert bypass == {"bloom_user": False, "bloom_writer": False, "authenticated": False}
    pg_conn.rollback()


_DIRECT_WRITE = {
    "cyl_trait_sources": "INSERT INTO cyl_trait_sources (name) VALUES ('forged')",
    "cyl_scan_traits": "INSERT INTO cyl_scan_traits (scan_id, source_id) VALUES (%(scan)s, %(src)s)",
    "cyl_scan_intermediates": (
        "INSERT INTO cyl_scan_intermediates "
        "(source_id, scan_id, kind, root_type, s3_location) "
        "VALUES (%(src)s, %(scan)s, 'predictions_slp', 'primary', 's3://b/k.slp')"
    ),
}


@pytest.mark.parametrize("role", ["bloom_writer", "bloom_user"])
@pytest.mark.parametrize("table", TABLES)
def test_direct_write_is_denied(pg_conn, role, table):
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO cyl_trait_sources (name) VALUES ('p') RETURNING id")
        src = cur.fetchone()[0]
        cur.execute("INSERT INTO cyl_scans DEFAULT VALUES RETURNING id")
        scan = cur.fetchone()[0]
        cur.execute(f"SET LOCAL ROLE {role}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(_DIRECT_WRITE[table], {"src": src, "scan": scan})
        # the denied INSERT aborts the txn; pg_conn.rollback() resets the LOCAL ROLE
    pg_conn.rollback()


@pytest.mark.parametrize("table", ["cyl_trait_sources", "cyl_scan_traits"])
def test_authenticated_direct_insert_denied_on_older_tables(pg_conn, table):
    with pg_conn.cursor() as cur:
        cur.execute("INSERT INTO cyl_trait_sources (name) VALUES ('p') RETURNING id")
        src = cur.fetchone()[0]
        cur.execute("INSERT INTO cyl_scans DEFAULT VALUES RETURNING id")
        scan = cur.fetchone()[0]
        cur.execute("SET LOCAL ROLE authenticated")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            cur.execute(_DIRECT_WRITE[table], {"src": src, "scan": scan})
    pg_conn.rollback()


def test_rpc_succeeds_as_bloom_writer(pg_conn):
    # The same write the direct path denies succeeds through the RPC (SECURITY DEFINER).
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        cur.execute("SET LOCAL ROLE bloom_writer")
        res = _call(cur, _envelope(imgs, idempotency_key="bw", traits=[_trait("t", 1.0)]))
        assert res["was_noop"] is False and res["trait_count"] == 1
        cur.execute("RESET ROLE")
    pg_conn.rollback()


@pytest.mark.parametrize("table", TABLES)
def test_bloom_writer_retains_select(pg_conn, table):
    with pg_conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE bloom_writer")
        cur.execute(f"SELECT count(*) FROM public.{table}")
        assert cur.fetchone()[0] is not None
        cur.execute("RESET ROLE")
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Migration idempotency + rollback fidelity
# --------------------------------------------------------------------------- #

_TS = "20260630180000_add_cyl_writeback_rpc"
MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_TS}.sql"
ROLLBACK = REPO_ROOT / "supabase" / "rollbacks" / f"{_TS}_rollback.sql"


def _sql_body(path: Path) -> str:
    """The migration/rollback body minus its BEGIN;/COMMIT; wrapper, applied inside
    the fixture's uncommitted transaction (CRLF-safe, matching the change-C pattern)."""
    return "\n".join(
        line for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


def test_migration_body_is_idempotent(pg_conn):
    # Re-applying the migration on top of the already-applied state is a clean no-op
    # (CREATE OR REPLACE FUNCTION / DROP POLICY IF EXISTS / REVOKE / GRANT / ALTER OWNER).
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION))
        cur.execute("SELECT 1 FROM pg_proc WHERE proname='insert_cyl_result_envelope'")
        assert cur.fetchone() is not None
    pg_conn.rollback()


def test_rollback_restores_prior_policies(pg_conn):
    """Apply the rollback body in an uncommitted txn; assert the function is dropped and
    every previously-dropped policy is recreated with matching qual/with_check; ROLLBACK."""
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(ROLLBACK))

        cur.execute("SELECT 1 FROM pg_proc WHERE proname='insert_cyl_result_envelope'")
        assert cur.fetchone() is None, "rollback did not drop the RPC"

        cur.execute(
            "SELECT tablename, policyname, cmd, qual, with_check FROM pg_policies "
            "WHERE schemaname='public' AND ("
            "  (policyname LIKE 'writer_insert_%' OR policyname LIKE 'writer_update_%') "
            "  OR policyname LIKE 'Authenticated users can insert%')"
            " AND tablename IN ('cyl_trait_sources','cyl_scan_traits','cyl_scan_intermediates')"
        )
        rows = {(t, p): (cmd, qual, wc) for t, p, cmd, qual, wc in cur.fetchall()}

        # legacy authenticated INSERT on the two older tables
        for tbl in ("cyl_trait_sources", "cyl_scan_traits"):
            key = (tbl, f"Authenticated users can insert {tbl}")
            assert key in rows, f"missing recreated legacy policy {key}"
            assert rows[key][0] == "INSERT" and rows[key][2] == "true"

        # bloom_writer INSERT (with_check) + UPDATE (BOTH qual and with_check) on all three
        for tbl in ("cyl_trait_sources", "cyl_scan_traits", "cyl_scan_intermediates"):
            ins = (tbl, f"writer_insert_{tbl}")
            upd = (tbl, f"writer_update_{tbl}")
            assert ins in rows and rows[ins][0] == "INSERT" and rows[ins][2] == "true"
            assert upd in rows and rows[upd][0] == "UPDATE"
            assert rows[upd][1] == "true" and rows[upd][2] == "true", (
                f"{upd} must restore BOTH USING and WITH CHECK"
            )
    pg_conn.rollback()
