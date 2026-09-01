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
PINNED_VERSION = "0.1.0a3"


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
              drop_provenance=False, drop_inputs=False, drop_contract_version=False):
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
    if drop_contract_version:
        prov.pop("contract_version")
    env = {"provenance": prov, "traits": traits or [], "blobs": blobs or []}
    if drop_provenance:
        env.pop("provenance")
    return env


def _call(cur, envelope, *, argo_workflow_name=None):
    """Call the RPC, returning the parsed jsonb summary. `argo_workflow_name`
    exercises the new (fix-cyl-pipeline-run-scan-status) optional second
    parameter; omitted, it relies on the RPC's own DEFAULT NULL, matching the
    existing manual-invocation call shape exactly."""
    cur.execute(
        f"SELECT {RPC}(%s::jsonb, %s)", (json.dumps(envelope), argo_workflow_name)
    )
    res = cur.fetchone()[0]
    return json.loads(res) if isinstance(res, str) else res


def _seed_run_scan_for_writeback(cur, scan_id: int, argo_workflow_name: str, **overrides) -> int:
    """Seed a cyl_pipeline_runs + cyl_pipeline_run_scans row pair for `scan_id`,
    already dispatched under `argo_workflow_name` — the state complete_cyl_pipeline_batch
    leaves a scan in before write-back ever runs. Mirrors test_cyl_pipeline_dispatch.py's
    own `_seed_run`/`_seed_run_scan` helpers (this file has no such helper today)."""
    cur.execute(
        "INSERT INTO cyl_pipeline_runs (target_level, target_id, params, requested_by) "
        "VALUES ('scan', %s, '{}'::jsonb, '00000000-0000-0000-0000-000000000001') RETURNING id",
        (scan_id,),
    )
    run_id = cur.fetchone()[0]
    fields = {
        "run_id": run_id, "scan_id": scan_id,
        "argo_workflow_name": argo_workflow_name, "status": "queued",
        **overrides,
    }
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(["%s"] * len(fields))
    cur.execute(
        f"INSERT INTO cyl_pipeline_run_scans ({cols}) VALUES ({placeholders}) RETURNING id",
        list(fields.values()),
    )
    return run_id


def _run_scan_status(cur, argo_workflow_name: str, scan_id: int):
    cur.execute(
        "SELECT status, source_id FROM cyl_pipeline_run_scans "
        "WHERE argo_workflow_name = %s AND scan_id = %s",
        (argo_workflow_name, scan_id),
    )
    return cur.fetchone()


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


def test_bare_contract_version_accepted(pg_conn):
    # The emitter stamps the bare PEP 440 package version; it must be accepted.
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        res = _call(cur, _envelope(imgs, contract_version="0.1.0a3", idempotency_key="cvbare"))
        assert res["was_noop"] is False
    pg_conn.rollback()


def test_v_prefixed_contract_version_accepted(pg_conn):
    # The v-prefixed git-tag/$id form normalizes to the same pinned version.
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        res = _call(cur, _envelope(imgs, contract_version="v0.1.0a3", idempotency_key="cvvpref"))
        assert res["was_noop"] is False
    pg_conn.rollback()


@pytest.mark.parametrize("ver", ["0.1.0a2", "v0.1.0a2"])
def test_a2_contract_version_rejected(pg_conn, ver):
    # Hard cutover: the previously pinned version (either form) is refused, not
    # accepted as a compatibility fallback. Note `v0.1.0a2` is the load-bearing
    # revert-detector -- it was ACCEPTED by the pre-a3 strict RPC, so this case
    # fails if the a3 migration is reverted; bare `0.1.0a2` rejects either way.
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        with pytest.raises(psycopg.errors.RaiseException):
            _call(cur, _envelope(imgs, contract_version=ver, idempotency_key="cva2"))
    pg_conn.rollback()


def test_contract_version_mismatch_rejected(pg_conn):
    # An arbitrary unrelated version is rejected.
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        with pytest.raises(psycopg.errors.RaiseException):
            _call(cur, _envelope(imgs, contract_version="v0.0.0a0", idempotency_key="cv"))
    pg_conn.rollback()


@pytest.mark.parametrize("ver", ["V0.1.0a3", "0.1.0a3 ", "0.1.0a30", "vv0.1.0a3"])
def test_version_boundary_forms_rejected(pg_conn, ver):
    # Normalization strips a single lowercase leading `v` only: uppercase V, a
    # doubled vv, trailing whitespace, and near-miss versions all reject.
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        with pytest.raises(psycopg.errors.RaiseException):
            _call(cur, _envelope(imgs, contract_version=ver, idempotency_key="cvbound"))
    pg_conn.rollback()


@pytest.mark.parametrize("ver", [3, True, {"a": 1}, [1, 2]], ids=["number", "bool", "object", "array"])
def test_non_string_contract_version_rejected(pg_conn, ver):
    # A non-string JSON contract_version serializes via `->>` (e.g. 3 -> '3',
    # {"a":1} -> '{"a": 1}') and fails the match cleanly -- no crash, no silent
    # accept.
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        with pytest.raises(psycopg.errors.RaiseException):
            _call(cur, _envelope(imgs, contract_version=ver, idempotency_key="cvns"))
    pg_conn.rollback()


@pytest.mark.parametrize(
    "kwargs", [{"contract_version": ""}, {"drop_contract_version": True}], ids=["empty", "absent"]
)
def test_absent_or_empty_contract_version_rejected(pg_conn, kwargs):
    # NULL/absent collapses to '' via coalesce before the compare, so it fails the
    # match rather than passing (the one way a naive `= pinned` would get wrong).
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        with pytest.raises(psycopg.errors.RaiseException):
            _call(cur, _envelope(imgs, idempotency_key="cvae", **kwargs))
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


# Only a JSON *number* is a value candidate. JSON null, the non-conforming string
# forms ("NaN"/"Infinity"/"abc"/"1.5"), and a finite number out of real range all
# map to SQL NULL (the jsonb_typeof='number' guard + overflow-on-cast).
@pytest.mark.parametrize("bad_value", [None, "NaN", "Infinity", "abc", "1.5", 1e40])
def test_non_finite_or_overflow_value_is_null(pg_conn, bad_value):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        env = _envelope(imgs, idempotency_key="fin", traits=[_trait("tt", bad_value)])
        _call(cur, env)
        rows = _trait_rows(cur, "fin")
        assert rows[0][1] is None, f"{bad_value!r} should normalize to NULL, got {rows[0][1]!r}"
    pg_conn.rollback()


def test_duplicate_trait_name_in_envelope_rejected(pg_conn):
    # Intra-envelope duplicate (scan, trait) is a malformed envelope -> rejected
    # (no silent dedup; symmetric with blobs). The source gate handles re-delivery.
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        env = _envelope(imgs, idempotency_key="dupt",
                        traits=[_trait("t", 1.0), _trait("t", 2.0)])
        with pytest.raises(psycopg.errors.UniqueViolation):
            _call(cur, env)
    pg_conn.rollback()


def test_duplicate_blob_in_envelope_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        env = _envelope(imgs, idempotency_key="dupb",
                        blobs=[_blob(root_type="primary"), _blob(root_type="primary")])
        with pytest.raises(psycopg.errors.UniqueViolation):
            _call(cur, env)
    pg_conn.rollback()


def test_traits_not_an_array_is_rejected_cleanly(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        env = _envelope(imgs, idempotency_key="ta")
        env["traits"] = {"not": "an array"}
        with pytest.raises(psycopg.errors.RaiseException):
            _call(cur, env)
    pg_conn.rollback()


def test_null_trait_name_is_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        env = _envelope(imgs, idempotency_key="nn",
                        traits=[{"scan_key": "SK1", "value": 1.0}])  # no name
        with pytest.raises(psycopg.errors.RaiseException):
            _call(cur, env)
    pg_conn.rollback()


def test_non_numeric_file_size_is_rejected_cleanly(pg_conn):
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        env = _envelope(imgs, idempotency_key="fs",
                        blobs=[_blob(file_size="not-a-number")])
        with pytest.raises(psycopg.errors.RaiseException):
            _call(cur, env)
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
# fix-cyl-pipeline-run-scan-status — per-scan write-back status (bloom #696)
# --------------------------------------------------------------------------- #


def test_matching_argo_workflow_name_marks_scan_written(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs = _seed_scan(cur)
        _seed_run_scan_for_writeback(cur, scan_id, "wf-1")
        res = _call(cur, _envelope(imgs, idempotency_key="wf1"), argo_workflow_name="wf-1")
        status, source_id = _run_scan_status(cur, "wf-1", scan_id)
        assert status == "written"
        assert source_id == res["source_id"]
    pg_conn.rollback()


def test_noop_redelivery_with_argo_workflow_name_still_marks_written(pg_conn):
    # This RPC's own idempotent re-delivery never writes 'reused' — that value
    # stays reserved for the separate, unimplemented pre-dispatch skip-if-done
    # mechanism (cyl_pipeline_run_scans' own schema comment).
    with pg_conn.cursor() as cur:
        scan_id, imgs = _seed_scan(cur)
        _seed_run_scan_for_writeback(cur, scan_id, "wf-2")
        env = _envelope(imgs, idempotency_key="wf2")
        first = _call(cur, env, argo_workflow_name="wf-2")
        second = _call(cur, env, argo_workflow_name="wf-2")
        assert first["was_noop"] is False and second["was_noop"] is True
        status, source_id = _run_scan_status(cur, "wf-2", scan_id)
        assert status == "written"
        assert source_id == first["source_id"]
    pg_conn.rollback()


def test_omitting_argo_workflow_name_leaves_run_scans_untouched(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs = _seed_scan(cur)
        _seed_run_scan_for_writeback(cur, scan_id, "wf-3")
        _call(cur, _envelope(imgs, idempotency_key="wf3"))  # no argo_workflow_name
        status, source_id = _run_scan_status(cur, "wf-3", scan_id)
        assert status == "queued" and source_id is None
    pg_conn.rollback()


def test_nonmatching_argo_workflow_name_affects_zero_rows(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs = _seed_scan(cur)
        _seed_run_scan_for_writeback(cur, scan_id, "wf-4")
        res = _call(cur, _envelope(imgs, idempotency_key="wf4"), argo_workflow_name="wf-does-not-exist")
        assert res["was_noop"] is False  # write-back itself is unaffected
        status, source_id = _run_scan_status(cur, "wf-4", scan_id)
        assert status == "queued" and source_id is None
    pg_conn.rollback()


def test_rolled_back_call_does_not_leave_partial_status_update(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs = _seed_scan(cur)
        _seed_run_scan_for_writeback(cur, scan_id, "wf-5")
        # A raised RAISE EXCEPTION aborts the whole enclosing transaction, not
        # just the one statement — a SAVEPOINT lets this test recover and keep
        # querying within the same pg_conn transaction, matching how psycopg3
        # itself models a nested transaction.
        with pg_conn.transaction():
            with pytest.raises(psycopg.errors.RaiseException):
                with pg_conn.transaction():
                    _call(
                        cur,
                        _envelope(imgs, contract_version="v0.0.0a0", idempotency_key="wf5"),
                        argo_workflow_name="wf-5",
                    )
            status, source_id = _run_scan_status(cur, "wf-5", scan_id)
            assert status == "queued" and source_id is None
    pg_conn.rollback()


def test_late_delivery_after_already_failed_does_not_resurrect(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs = _seed_scan(cur)
        _seed_run_scan_for_writeback(cur, scan_id, "wf-6")
        cur.execute(
            "UPDATE cyl_pipeline_run_scans SET status = 'failed' "
            "WHERE argo_workflow_name = 'wf-6'"
        )
        res = _call(cur, _envelope(imgs, idempotency_key="wf6"), argo_workflow_name="wf-6")
        assert res["was_noop"] is False  # write-back itself still succeeds
        status, source_id = _run_scan_status(cur, "wf-6", scan_id)
        assert status == "failed"  # not resurrected to 'written'
        assert source_id is None  # never touched by the guarded UPDATE
    pg_conn.rollback()


def test_scan_already_written_is_left_untouched_by_a_second_batch(pg_conn):
    # Mirror scenario for fail_cyl_pipeline_run_scans_without_result's own
    # "already written" idempotency, from the write-back side: a row this RPC
    # already marked 'written' is not affected by anything else in the same
    # batch — sanity check that step 9's guard is scoped to THIS scan only.
    with pg_conn.cursor() as cur:
        scan_id, imgs = _seed_scan(cur)
        _seed_run_scan_for_writeback(cur, scan_id, "wf-7")
        _call(cur, _envelope(imgs, idempotency_key="wf7"), argo_workflow_name="wf-7")
        status_before, source_before = _run_scan_status(cur, "wf-7", scan_id)
        cur.execute("SELECT fail_cyl_pipeline_run_scans_without_result('wf-7', 'no result')")
        n = cur.fetchone()[0]
        status_after, source_after = _run_scan_status(cur, "wf-7", scan_id)
        assert n == 0
        assert (status_after, source_after) == (status_before, source_before) == ("written", source_before)
    pg_conn.rollback()


def test_writeback_and_rollup_connect_end_to_end(pg_conn):
    """Task 6 — the piece nothing else exercises together: seed a run with several
    scans under one argo_workflow_name, write back some via the RPC and reconcile
    the rest as failed, then compute done_count/failed_count exactly the way
    status_poller.py does (a plain COUNT ... WHERE status IN (...) over the real,
    now-populated cyl_pipeline_run_scans rows) and confirm update_cyl_pipeline_run_status
    stores what the real per-scan split actually is — not a mocked or hardcoded value."""
    with pg_conn.cursor() as cur:
        scan_ok1, imgs_ok1 = _seed_scan(cur)
        scan_ok2, imgs_ok2 = _seed_scan(cur)
        scan_fail, _imgs_fail = _seed_scan(cur)
        wf = "wf-e2e"
        run_id = _seed_run_scan_for_writeback(cur, scan_ok1, wf)
        # second and third scans join the SAME run/workflow
        cur.execute(
            "INSERT INTO cyl_pipeline_run_scans (run_id, scan_id, argo_workflow_name, status) "
            "VALUES (%s, %s, %s, 'queued')",
            (run_id, scan_ok2, wf),
        )
        cur.execute(
            "INSERT INTO cyl_pipeline_run_scans (run_id, scan_id, argo_workflow_name, status) "
            "VALUES (%s, %s, %s, 'queued')",
            (run_id, scan_fail, wf),
        )
        cur.execute("UPDATE cyl_pipeline_runs SET status = 'submitted' WHERE id = %s", (run_id,))

        _call(cur, _envelope(imgs_ok1, idempotency_key="e2e-1"), argo_workflow_name=wf)
        _call(cur, _envelope(imgs_ok2, idempotency_key="e2e-2"), argo_workflow_name=wf)
        cur.execute(f"SELECT {FAIL_RPC}(%s, %s)", (wf, "no envelope produced"))

        # Compute counts the same way status_poller.py's sweep_once does.
        cur.execute(
            "SELECT "
            "  count(*) FILTER (WHERE status IN ('written', 'reused')), "
            "  count(*) FILTER (WHERE status = 'failed') "
            "FROM cyl_pipeline_run_scans WHERE run_id = %s",
            (run_id,),
        )
        done_count, failed_count = cur.fetchone()
        assert (done_count, failed_count) == (2, 1)

        cur.execute(
            "SELECT update_cyl_pipeline_run_status(%s, %s, %s, %s)",
            (run_id, "partial", done_count, failed_count),
        )
        cur.execute(
            "SELECT status, done_count, failed_count FROM cyl_pipeline_runs WHERE id = %s",
            (run_id,),
        )
        run_status, run_done, run_failed = cur.fetchone()
        assert (run_status, run_done, run_failed) == ("partial", 2, 1)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# fix-cyl-pipeline-run-scan-status — fail_cyl_pipeline_run_scans_without_result
# --------------------------------------------------------------------------- #

FAIL_RPC = "public.fail_cyl_pipeline_run_scans_without_result"


def test_fail_rpc_marks_queued_scan_failed(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, _ = _seed_scan(cur)
        _seed_run_scan_for_writeback(cur, scan_id, "wf-f1")
        cur.execute(f"SELECT {FAIL_RPC}('wf-f1', 'no envelope produced')")
        n = cur.fetchone()[0]
        assert n == 1
        cur.execute(
            "SELECT status, error_message FROM cyl_pipeline_run_scans WHERE argo_workflow_name='wf-f1'"
        )
        status, error_message = cur.fetchone()
        assert status == "failed" and error_message == "no envelope produced"
    pg_conn.rollback()


def test_fail_rpc_leaves_already_written_scan_untouched(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, imgs = _seed_scan(cur)
        _seed_run_scan_for_writeback(cur, scan_id, "wf-f2")
        _call(cur, _envelope(imgs, idempotency_key="wff2"), argo_workflow_name="wf-f2")
        cur.execute(f"SELECT {FAIL_RPC}('wf-f2', 'no envelope produced')")
        n = cur.fetchone()[0]
        assert n == 0
        status, _src = _run_scan_status(cur, "wf-f2", scan_id)
        assert status == "written"
    pg_conn.rollback()


def test_fail_rpc_called_twice_is_a_harmless_noop(pg_conn):
    with pg_conn.cursor() as cur:
        scan_id, _ = _seed_scan(cur)
        _seed_run_scan_for_writeback(cur, scan_id, "wf-f3")
        cur.execute(f"SELECT {FAIL_RPC}('wf-f3', 'first')")
        assert cur.fetchone()[0] == 1
        cur.execute(
            "SELECT status, error_message, updated_at FROM cyl_pipeline_run_scans "
            "WHERE argo_workflow_name='wf-f3'"
        )
        first_state = cur.fetchone()
        cur.execute(f"SELECT {FAIL_RPC}('wf-f3', 'second')")
        assert cur.fetchone()[0] == 0
        cur.execute(
            "SELECT status, error_message, updated_at FROM cyl_pipeline_run_scans "
            "WHERE argo_workflow_name='wf-f3'"
        )
        assert cur.fetchone() == first_state  # unchanged by the second, no-op call
    pg_conn.rollback()


def test_fail_rpc_unmatched_workflow_name_returns_zero(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(f"SELECT {FAIL_RPC}('wf-does-not-exist', 'x')")
        assert cur.fetchone()[0] == 0
    pg_conn.rollback()


def test_fail_rpc_execute_denied_to_every_role_except_bloom_workflows(pg_conn):
    with pg_conn.cursor() as cur:
        sig = f"{FAIL_RPC}(text, text)"
        for role in ["anon", "authenticated", "bloom_user", "bloom_writer", "bloom_admin"]:
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')", (role, sig))
            assert cur.fetchone()[0] is False, f"{role} must not hold EXECUTE"
        cur.execute("SELECT has_function_privilege('public', %s, 'EXECUTE')", (sig,))
        assert cur.fetchone()[0] is False, "PUBLIC must not execute the RPC"
        cur.execute("SELECT has_function_privilege('bloom_workflows', %s, 'EXECUTE')", (sig,))
        assert cur.fetchone()[0] is True
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
    # Signature is (jsonb, text) as of fix-cyl-pipeline-run-scan-status — the
    # 1-arg overload no longer exists (dropped by the new migration), so
    # has_function_privilege against the old signature would raise, not fail.
    with pg_conn.cursor() as cur:
        cur.execute("SELECT has_function_privilege('public', %s, 'EXECUTE')",
                    (f"{RPC}(jsonb, text)",))
        assert cur.fetchone()[0] is False, "PUBLIC must not execute the RPC"
        for role in ["bloom_writer", "service_role", "bloom_admin", "bloom_workflows"]:
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                        (role, f"{RPC}(jsonb, text)"))
            assert cur.fetchone()[0] is True, f"{role} should hold EXECUTE"
        for role in ["bloom_user", "bloom_agent"]:
            cur.execute("SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                        (role, f"{RPC}(jsonb, text)"))
            assert cur.fetchone()[0] is False, f"{role} must not hold EXECUTE"
    pg_conn.rollback()


def test_bloom_workflows_can_call_the_writeback_rpc(pg_conn):
    # The scoped, non-interactive service identity (A4 cluster write-back pods) can call
    # the RPC exactly like bloom_writer, via the SECURITY DEFINER owner (see
    # test_rpc_succeeds_as_bloom_writer).
    with pg_conn.cursor() as cur:
        _, imgs = _seed_scan(cur)
        cur.execute("SET LOCAL ROLE bloom_workflows")
        res = _call(cur, _envelope(imgs, idempotency_key="wf", traits=[_trait("t", 1.0)]))
        assert res["was_noop"] is False and res["trait_count"] == 1
        cur.execute("RESET ROLE")
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


@pytest.mark.parametrize("role", ["bloom_writer", "bloom_user", "bloom_workflows"])
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
    every previously-dropped policy is recreated with matching qual/with_check; ROLLBACK.

    Rollbacks must be applied in reverse-chronological order: this migration's own
    rollback only ever knew how to drop the 1-arg signature it originally created,
    but fix-cyl-pipeline-run-scan-status's later migration changed the live function
    to a 2-arg signature — so that later migration's own rollback must run FIRST
    (restoring the 1-arg signature) before this rollback can actually remove it."""
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(ROLLBACK_SCAN_STATUS))
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


# --------------------------------------------------------------------------- #
# a3 contract-version re-pin migration (repin-cyl-contract-a3, #393)
# --------------------------------------------------------------------------- #

_TS_A3 = "20260706170000_cyl_writeback_contract_a3"
MIGRATION_A3 = REPO_ROOT / "supabase" / "migrations" / f"{_TS_A3}.sql"
ROLLBACK_A3 = REPO_ROOT / "supabase" / "rollbacks" / f"{_TS_A3}_rollback.sql"


def _call_1arg(cur, envelope):
    """Call the RPC via its ORIGINAL 1-arg signature explicitly. Since
    fix-cyl-pipeline-run-scan-status's migration made the live
    insert_cyl_result_envelope 2-arg, `_call`'s 2-positional-argument shape
    always resolves to that overload — these two a3-specific tests apply
    MIGRATION_A3/ROLLBACK_A3's bodies, both of which are CREATE OR REPLACE on
    the OLD 1-arg signature, so they must call that exact overload to
    actually exercise what they just applied, not the unrelated 2-arg one
    that's still live from this change's own migration."""
    cur.execute(f"SELECT {RPC}(%s::jsonb)", (json.dumps(envelope),))
    res = cur.fetchone()[0]
    return json.loads(res) if isinstance(res, str) else res


def test_a3_migration_body_is_idempotent(pg_conn):
    # Re-applying the a3 migration on top of the applied state is a clean no-op
    # (CREATE OR REPLACE FUNCTION / ALTER OWNER / REVOKE / GRANT), and the RPC still
    # accepts the pinned a3 contract_version afterward.
    #
    # First roll back fix-cyl-pipeline-run-scan-status's later 2-arg signature (its
    # DEFAULT NULL second parameter would otherwise let a single-jsonb-argument call
    # ambiguously match either overload once a3's own CREATE OR REPLACE recreates a
    # 1-arg overload here) — a3's migration/rollback bodies predate that later
    # migration and know nothing about it, so this test must restore the "just a3"
    # single-overload world before exercising them in isolation.
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(ROLLBACK_SCAN_STATUS))
        cur.execute(_sql_body(MIGRATION_A3))
        cur.execute(_sql_body(MIGRATION_A3))  # second apply: must be a no-op, not an error
        _, imgs = _seed_scan(cur)
        res = _call_1arg(cur, _envelope(imgs, contract_version="0.1.0a3", idempotency_key="a3idem"))
        assert res["was_noop"] is False
    pg_conn.rollback()


def test_a3_rollback_restores_strict_a2(pg_conn):
    """Apply the a3 body then its rollback in an uncommitted txn; assert the function is
    restored to the strict v0.1.0a2 posture — the a3 version it just accepted is now
    rejected — and the function still exists (the a3 change only replaced its body).

    Rolls back fix-cyl-pipeline-run-scan-status's later signature first — see
    test_a3_migration_body_is_idempotent's comment for why."""
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(ROLLBACK_SCAN_STATUS))
        cur.execute(_sql_body(MIGRATION_A3))   # a3 body present
        cur.execute(_sql_body(ROLLBACK_A3))    # roll back to strict v0.1.0a2
        cur.execute("SELECT 1 FROM pg_proc WHERE proname='insert_cyl_result_envelope'")
        assert cur.fetchone() is not None, "a3 rollback must keep the function (body-only change)"
        _, imgs = _seed_scan(cur)
        with pytest.raises(psycopg.errors.RaiseException):
            _call_1arg(cur, _envelope(imgs, contract_version="0.1.0a3", idempotency_key="a3rb"))
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# fix-cyl-pipeline-run-scan-status migration (adds p_argo_workflow_name +
# fail_cyl_pipeline_run_scans_without_result)
# --------------------------------------------------------------------------- #

_TS_SCAN_STATUS = "20260901000000_add_cyl_writeback_run_scan_status"
MIGRATION_SCAN_STATUS = REPO_ROOT / "supabase" / "migrations" / f"{_TS_SCAN_STATUS}.sql"
ROLLBACK_SCAN_STATUS = REPO_ROOT / "supabase" / "rollbacks" / f"{_TS_SCAN_STATUS}_rollback.sql"


def test_scan_status_migration_body_is_idempotent(pg_conn):
    # Re-applying the migration body on top of the already-applied state must be a
    # clean no-op: DROP FUNCTION IF EXISTS on the (by-then-gone) 1-arg signature is
    # a no-op, and CREATE OR REPLACE on the 2-arg signature replaces cleanly.
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION_SCAN_STATUS))
        cur.execute(_sql_body(MIGRATION_SCAN_STATUS))
        cur.execute(
            "SELECT 1 FROM pg_proc WHERE proname='insert_cyl_result_envelope' "
            "AND pronargs=2"
        )
        assert cur.fetchone() is not None
        cur.execute("SELECT 1 FROM pg_proc WHERE proname='fail_cyl_pipeline_run_scans_without_result'")
        assert cur.fetchone() is not None
    pg_conn.rollback()


def test_scan_status_rollback_restores_1arg_signature(pg_conn):
    """Apply the migration then its rollback in an uncommitted txn: the 2-arg
    signature and the new RPC are gone, the 1-arg signature is back and callable."""
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION_SCAN_STATUS))
        cur.execute(_sql_body(ROLLBACK_SCAN_STATUS))
        cur.execute(
            "SELECT 1 FROM pg_proc WHERE proname='insert_cyl_result_envelope' AND pronargs=2"
        )
        assert cur.fetchone() is None, "rollback did not remove the 2-arg signature"
        cur.execute(
            "SELECT 1 FROM pg_proc WHERE proname='insert_cyl_result_envelope' AND pronargs=1"
        )
        assert cur.fetchone() is not None, "rollback did not restore the 1-arg signature"
        cur.execute("SELECT 1 FROM pg_proc WHERE proname='fail_cyl_pipeline_run_scans_without_result'")
        assert cur.fetchone() is None, "rollback did not drop the new RPC"
        # the restored 1-arg signature is genuinely callable via the old shape
        _, imgs = _seed_scan(cur)
        cur.execute(f"SELECT {RPC}(%s::jsonb)", (json.dumps(_envelope(imgs, idempotency_key="rb1")),))
        res = cur.fetchone()[0]
        res = json.loads(res) if isinstance(res, str) else res
        assert res["was_noop"] is False
    pg_conn.rollback()
