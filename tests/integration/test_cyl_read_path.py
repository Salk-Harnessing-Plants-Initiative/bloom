"""
Integration tests for the A2 read-path change (`add-cyl-trait-read-source-aware`).

Makes the cyl trait READ path source-aware now that reprocessing a scan mints a new
`cyl_trait_sources` row (write-back RPC, #371), so a scan can carry MULTIPLE sources:

- `cyl_scan_traits_source` — substrate view exposing the source dimension + `is_latest`
  (`source_id IS NOT DISTINCT FROM max(source_id) OVER (PARTITION BY scan_id)`).
- `cyl_scan_traits_latest` — `is_latest` rows (canonical latest-per-scan surface).
- `get_scan_traits(experiment_id_, trait_name_, source_id_ DEFAULT NULL, run_id_ DEFAULT NULL)`
  — latest by default; pin a source; group by pipeline run ("as of run X", deduped to the
  latest delivery within the run); both optional args set → error.
- `cyl_scan_trait_names` — distinct non-null trait names present in latest data.

LOCAL ONLY: the `pg_conn` fixture connects to 127.0.0.1 on POSTGRES_HOST_PORT as
`supabase_admin` (BYPASSRLS) and every test rolls back. RLS is exercised with `SET LOCAL
ROLE`. The PostgREST HTTP sub-test skips when the gateway is unreachable (dev has none); CI's
`compose-health-check` runs it against the full stack. Sources are seeded through the
write-back RPC (`insert_cyl_result_envelope`); legacy NULL-source / NULL-trait rows are seeded
by direct INSERT (allowed for supabase_admin, which bypasses the change-E lockdown).
"""

import itertools
import json
import re
import uuid
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

REPO_ROOT = Path(__file__).parent.parent.parent
RPC = "public.insert_cyl_result_envelope"
PINNED_VERSION = "v0.1.0a2"

_TS = "20260701000000_cyl_trait_read_source_aware"
MIGRATION = REPO_ROOT / "supabase" / "migrations" / f"{_TS}.sql"
ROLLBACK = REPO_ROOT / "supabase" / "rollbacks" / f"{_TS}_rollback.sql"

# The 10 result columns get_scan_traits has always returned, in order.
RESULT_COLUMNS = [
    "scan_id",
    "date_scanned",
    "plant_age_days",
    "wave_number",
    "plant_id",
    "germ_day",
    "plant_qr_code",
    "accession_name",
    "trait_name",
    "trait_value",
]

_uniq = itertools.count(1)


# --------------------------------------------------------------------------- #
# Seeding helpers
# --------------------------------------------------------------------------- #


def _seed_experiment(cur):
    """species → cyl_experiments → cyl_waves. Returns (experiment_id, wave_id)."""
    n = next(_uniq)
    cur.execute("INSERT INTO species DEFAULT VALUES RETURNING id")
    species_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_experiments (name, species_id) VALUES (%s, %s) RETURNING id",
        (f"exp-{n}", species_id),
    )
    experiment_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_waves (experiment_id, number) VALUES (%s, %s) RETURNING id",
        (experiment_id, 1),
    )
    return experiment_id, cur.fetchone()[0]


def _seed_scan_in(cur, wave_id, *, accession=None, n_images=2, plant_age_days=10):
    """accessions → cyl_plants → cyl_scans → cyl_images under an existing wave.

    accessions.name is NOT NULL + UNIQUE, so distinct names are generated per scan.
    Returns (scan_id, image_ids).
    """
    # accessions.name is UNIQUE; a uuid token keeps it collision-proof across runs (some
    # committed leftovers can exist), while `accession` lets a test pin a name for ordering.
    tok = uuid.uuid4().hex[:12]
    acc = accession if accession is not None else f"acc-{tok}"
    cur.execute("INSERT INTO accessions (name) VALUES (%s) RETURNING id", (acc,))
    accession_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_plants (wave_id, accession_id, germ_day, qr_code) "
        "VALUES (%s, %s, %s, %s) RETURNING id",
        (wave_id, accession_id, 5, f"qr-{tok}"),
    )
    plant_id = cur.fetchone()[0]
    cur.execute(
        "INSERT INTO cyl_scans (plant_id, date_scanned, plant_age_days) "
        "VALUES (%s, %s, %s) RETURNING id",
        (plant_id, "2026-01-01", plant_age_days),
    )
    scan_id = cur.fetchone()[0]
    img_ids = []
    for _ in range(n_images):
        cur.execute(
            "INSERT INTO cyl_images (scan_id) VALUES (%s) RETURNING id", (scan_id,)
        )
        img_ids.append(cur.fetchone()[0])
    return scan_id, img_ids


def _seed_experiment_scan(cur, *, accession=None):
    """Convenience: one experiment with one scan. Returns (experiment_id, scan_id, image_ids)."""
    experiment_id, wave_id = _seed_experiment(cur)
    scan_id, img_ids = _seed_scan_in(cur, wave_id, accession=accession)
    return experiment_id, scan_id, img_ids


def _trait(name, value, *, scan_key="SK1"):
    return {"name": name, "scan_key": scan_key, "value": value}


def _envelope(
    image_ids, *, idempotency_key, pipeline_run_id=None, scan_key="SK1", traits=None
):
    prov = {
        "contract_version": PINNED_VERSION,
        "scan_key": scan_key,
        "idempotency_key": idempotency_key,
        "inputs": {"image_ids": [str(i) for i in image_ids]},
    }
    if pipeline_run_id is not None:
        prov["pipeline_run_id"] = pipeline_run_id
    return {"provenance": prov, "traits": traits or [], "blobs": []}


def _call(cur, envelope):
    cur.execute(f"SELECT {RPC}(%s::jsonb)", (json.dumps(envelope),))
    res = cur.fetchone()[0]
    return json.loads(res) if isinstance(res, str) else res


def _deliver(cur, img_ids, label, *, run=None, traits):
    """One write-back delivery; returns its source_id. The idempotency key is made globally
    unique (uuid) so a delivery never no-ops against a committed key from an earlier run;
    `label` is just a readable prefix. Distinct calls => distinct sources."""
    key = f"{label}-{uuid.uuid4().hex}"
    _call(
        cur, _envelope(img_ids, idempotency_key=key, pipeline_run_id=run, traits=traits)
    )
    cur.execute("SELECT id FROM cyl_trait_sources WHERE idempotency_key=%s", (key,))
    return cur.fetchone()[0]


def _register_trait(cur, name):
    cur.execute(
        "INSERT INTO cyl_traits (name) VALUES (%s) ON CONFLICT (name) DO NOTHING",
        (name,),
    )
    cur.execute("SELECT id FROM cyl_traits WHERE name=%s", (name,))
    return cur.fetchone()[0]


def _get_scan_traits(cur, experiment_id, trait_name, *, source_id=None, run_id=None):
    cur.execute(
        "SELECT scan_id, trait_value FROM get_scan_traits(%s, %s, %s, %s)",
        (experiment_id, trait_name, source_id, run_id),
    )
    return cur.fetchall()


def _sql_body(path: Path) -> str:
    """Migration/rollback body minus its BEGIN;/COMMIT; wrapper (CRLF-safe)."""
    return "\n".join(
        line
        for line in path.read_text().splitlines()
        if not re.match(r"^\s*(BEGIN|COMMIT)\s*;\s*$", line, re.IGNORECASE)
    )


# --------------------------------------------------------------------------- #
# Seeding linchpin
# --------------------------------------------------------------------------- #


def test_two_sources_one_scan_seed(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        s1 = _deliver(cur, imgs, "orig", run="run-1", traits=[_trait("length", 10.0)])
        s2 = _deliver(cur, imgs, "reproc", run="run-2", traits=[_trait("length", 20.0)])
        cur.execute(
            "SELECT count(DISTINCT source_id), max(source_id) FROM cyl_scan_traits WHERE scan_id=%s",
            (scan_id,),
        )
        n_sources, max_src = cur.fetchone()
        assert n_sources == 2 and s1 < s2 and max_src == s2
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Requirement: Canonical source-aware trait view
# --------------------------------------------------------------------------- #


def test_source_view_exposes_source_dimension(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        src = _deliver(cur, imgs, "k", run="run-9", traits=[_trait("length", 3.5)])
        cur.execute(
            "SELECT scan_id, trait_name, value, source_id, source_name, pipeline_run_id "
            "FROM cyl_scan_traits_source WHERE scan_id=%s",
            (scan_id,),
        )
        row = cur.fetchone()
        assert row == (scan_id, "length", 3.5, src, "run-9", "run-9")
    pg_conn.rollback()


def test_is_latest_marks_max_source(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        s1 = _deliver(cur, imgs, "old", traits=[_trait("length", 1.0)])
        s2 = _deliver(cur, imgs, "new", traits=[_trait("length", 2.0)])
        cur.execute(
            "SELECT source_id, is_latest FROM cyl_scan_traits_source WHERE scan_id=%s ORDER BY source_id",
            (scan_id,),
        )
        assert cur.fetchall() == [(s1, False), (s2, True)]
    pg_conn.rollback()


def test_legacy_null_source_is_latest(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, _ = _seed_experiment_scan(cur)
        tid = _register_trait(cur, "legacy")
        cur.execute(
            "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) VALUES (%s, NULL, %s, %s)",
            (scan_id, tid, 4.2),
        )
        cur.execute(
            "SELECT is_latest FROM cyl_scan_traits_source WHERE scan_id=%s AND source_id IS NULL",
            (scan_id,),
        )
        assert cur.fetchone() == (True,)
    pg_conn.rollback()


def test_pipeline_run_id_null_when_absent(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "nopr", run=None, traits=[_trait("length", 1.0)])
        cur.execute(
            "SELECT pipeline_run_id FROM cyl_scan_traits_source WHERE scan_id=%s",
            (scan_id,),
        )
        assert cur.fetchone() == (None,)
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Requirement: Latest-source-by-default scan trait reads
# --------------------------------------------------------------------------- #


def test_default_returns_latest_value_only(pg_conn):
    with pg_conn.cursor() as cur:
        exp, scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "old", run="r1", traits=[_trait("length", 10.0)])
        _deliver(cur, imgs, "new", run="r2", traits=[_trait("length", 20.0)])
        rows = _get_scan_traits(cur, exp, "length")
        assert rows == [(scan_id, 20.0)]  # latest only, no duplicate
    pg_conn.rollback()


def test_no_cross_source_mixing(pg_conn):
    with pg_conn.cursor() as cur:
        exp, scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(
            cur, imgs, "old", run="r1", traits=[_trait("A", 1.0), _trait("B", 2.0)]
        )
        _deliver(
            cur, imgs, "new", run="r2", traits=[_trait("A", 10.0)]
        )  # latest lacks B
        assert _get_scan_traits(cur, exp, "A") == [(scan_id, 10.0)]
        assert (
            _get_scan_traits(cur, exp, "B") == []
        )  # not backfilled from the older source
    pg_conn.rollback()


def test_latest_view_no_duplicate_rows(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "old", traits=[_trait("length", 1.0)])
        _deliver(cur, imgs, "new", traits=[_trait("length", 2.0)])
        cur.execute(
            "SELECT count(*) FROM cyl_scan_traits_latest WHERE scan_id=%s AND trait_name='length'",
            (scan_id,),
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_non_finite_latest_value_surfaced_as_null(pg_conn):
    with pg_conn.cursor() as cur:
        exp, scan_id, imgs = _seed_experiment_scan(cur)
        # JSON null -> RPC stores NULL (do not use float('nan'): json.dumps emits invalid JSON).
        _deliver(cur, imgs, "k", traits=[_trait("length", None)])
        assert _get_scan_traits(cur, exp, "length") == [(scan_id, None)]
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Requirement: Source-pinned and run-grouped get_scan_traits
# --------------------------------------------------------------------------- #


def test_backward_compatible_two_arg_call_returns_latest(pg_conn):
    with pg_conn.cursor() as cur:
        exp, scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "old", traits=[_trait("length", 10.0)])
        _deliver(cur, imgs, "new", traits=[_trait("length", 20.0)])
        cur.execute(
            "SELECT scan_id, trait_value FROM get_scan_traits(%s, %s)", (exp, "length")
        )
        assert cur.fetchall() == [(scan_id, 20.0)]
    pg_conn.rollback()


def test_backward_compatible_two_arg_call_over_postgrest(api, service_role_key):
    """Proves no PostgREST overload ambiguity (PGRST203) — the reason the 2-arg fn is dropped.
    PGRST203 is a function-resolution error that fires regardless of data, so no seeding/commit
    is needed. Only meaningful against the PostgREST gateway (CI compose-health-check); skips
    when the gateway is unreachable (dev has none)."""
    import urllib.error

    try:
        status, body = api(
            "/api/rest/v1/rpc/get_scan_traits",
            api_key=service_role_key,
            method="POST",
            data={"experiment_id_": 1, "trait_name_": "length"},
        )
    except (urllib.error.URLError, OSError) as e:
        pytest.skip(
            f"PostgREST gateway not reachable ({e}); CI compose-health-check covers this"
        )
    assert (
        status == 200
    ), f"expected 200 (2 named args bind the defaults), got {status}: {body}"
    # PGRST203 = "Could not choose the best candidate function" (overload ambiguity)
    assert not (isinstance(body, dict) and body.get("code") == "PGRST203"), body


def test_pin_older_source_returns_older_values(pg_conn):
    with pg_conn.cursor() as cur:
        exp, scan_id, imgs = _seed_experiment_scan(cur)
        old = _deliver(cur, imgs, "old", traits=[_trait("length", 10.0)])
        _deliver(cur, imgs, "new", traits=[_trait("length", 20.0)])
        assert _get_scan_traits(cur, exp, "length", source_id=old) == [(scan_id, 10.0)]
    pg_conn.rollback()


def test_pin_source_from_other_experiment_returns_nothing(pg_conn):
    with pg_conn.cursor() as cur:
        exp1, _, imgs1 = _seed_experiment_scan(cur)
        src1 = _deliver(cur, imgs1, "e1", traits=[_trait("length", 1.0)])
        exp2, _, imgs2 = _seed_experiment_scan(cur)
        _deliver(cur, imgs2, "e2", traits=[_trait("length", 2.0)])
        # pin exp1's source but query exp2 -> experiment filter must exclude it
        assert _get_scan_traits(cur, exp2, "length", source_id=src1) == []
    pg_conn.rollback()


def test_run_id_groups_experiment_by_pipeline_run(pg_conn):
    with pg_conn.cursor() as cur:
        exp, wave = _seed_experiment(cur)
        a, imgs_a = _seed_scan_in(cur, wave)
        b, imgs_b = _seed_scan_in(cur, wave)
        _deliver(cur, imgs_a, "a", run="run-1", traits=[_trait("length", 1.0)])
        _deliver(cur, imgs_b, "b", run="run-1", traits=[_trait("length", 2.0)])
        rows = _get_scan_traits(cur, exp, "length", run_id="run-1")
        assert sorted(rows) == sorted([(a, 1.0), (b, 2.0)])
    pg_conn.rollback()


def test_run_id_returns_run_values_even_after_supersede(pg_conn):
    with pg_conn.cursor() as cur:
        exp, wave = _seed_experiment(cur)
        a, imgs_a = _seed_scan_in(cur, wave)
        b, imgs_b = _seed_scan_in(cur, wave)
        _deliver(cur, imgs_a, "a1", run="run-1", traits=[_trait("length", 1.0)])
        _deliver(
            cur, imgs_a, "a2", run="run-2", traits=[_trait("length", 99.0)]
        )  # supersedes A
        _deliver(cur, imgs_b, "b1", run="run-1", traits=[_trait("length", 2.0)])
        rows = _get_scan_traits(cur, exp, "length", run_id="run-1")
        # A's run-1 value (1.0), NOT its newer run-2 value (99.0); plus B's run-1
        assert sorted(rows) == sorted([(a, 1.0), (b, 2.0)])
    pg_conn.rollback()


def test_run_id_dedups_duplicate_deliveries(pg_conn):
    with pg_conn.cursor() as cur:
        exp, scan_id, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "d1", run="run-x", traits=[_trait("length", 1.0)])
        _deliver(
            cur, imgs, "d2", run="run-x", traits=[_trait("length", 2.0)]
        )  # 2nd delivery, same run
        rows = _get_scan_traits(cur, exp, "length", run_id="run-x")
        assert rows == [(scan_id, 2.0)]  # single row = latest delivery within the run
    pg_conn.rollback()


def test_run_id_in_other_experiment_returns_nothing(pg_conn):
    with pg_conn.cursor() as cur:
        exp1, _, imgs1 = _seed_experiment_scan(cur)
        _deliver(cur, imgs1, "e1", run="shared-run", traits=[_trait("length", 1.0)])
        exp2, _, imgs2 = _seed_experiment_scan(cur)
        _deliver(cur, imgs2, "e2", run="other-run", traits=[_trait("length", 2.0)])
        # run in exp1; querying exp2 with exp1's run -> experiment filter excludes it
        assert _get_scan_traits(cur, exp2, "length", run_id="shared-run") == []
    pg_conn.rollback()


def test_run_id_matching_no_source_returns_no_rows(pg_conn):
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        _deliver(
            cur, imgs, "k", run=None, traits=[_trait("length", 1.0)]
        )  # no pipeline_run_id
        assert _get_scan_traits(cur, exp, "length", run_id="ghost") == []
    pg_conn.rollback()


def test_both_source_and_run_rejected(pg_conn):
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        src = _deliver(cur, imgs, "k", run="r", traits=[_trait("length", 1.0)])
        with pytest.raises(psycopg.errors.RaiseException):
            cur.execute(
                "SELECT * FROM get_scan_traits(%s, %s, %s, %s)",
                (exp, "length", src, "r"),
            )
    pg_conn.rollback()


def test_ordering_preserved(pg_conn):
    with pg_conn.cursor() as cur:
        # two accessions seeded out of alphabetical order -> returned Alpha before Zulu
        exp, wave = _seed_experiment(cur)
        _, imgs_z = _seed_scan_in(cur, wave, accession="Zulu")
        _, imgs_a = _seed_scan_in(cur, wave, accession="Alpha")
        _deliver(cur, imgs_z, "z", traits=[_trait("length", 1.0)])
        _deliver(cur, imgs_a, "a", traits=[_trait("length", 2.0)])
        cur.execute(
            "SELECT accession_name FROM get_scan_traits(%s, %s)", (exp, "length")
        )
        assert [r[0] for r in cur.fetchall()] == [
            "Alpha",
            "Zulu",
        ]  # ORDER BY accession_name
    pg_conn.rollback()


def test_result_column_names_are_exact(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT * FROM get_scan_traits(NULL::bigint, NULL::text) LIMIT 0")
        assert [d.name for d in cur.description] == RESULT_COLUMNS
    pg_conn.rollback()


def test_empty_experiment_returns_cleanly(pg_conn):
    with pg_conn.cursor() as cur:
        exp, _ = _seed_experiment(cur)  # experiment with no scans/traits
        assert _get_scan_traits(cur, exp, "length") == []
    pg_conn.rollback()


def test_legacy_null_source_scan_returned_by_default(pg_conn):
    with pg_conn.cursor() as cur:
        exp, scan_id, _ = _seed_experiment_scan(cur)
        tid = _register_trait(cur, "legacy")
        cur.execute(
            "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) VALUES (%s, NULL, %s, %s)",
            (
                scan_id,
                tid,
                7.5,
            ),  # dyadic: exact in real, so no float4->float8 widening drift
        )
        assert _get_scan_traits(cur, exp, "legacy") == [(scan_id, 7.5)]
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Requirement: Source-disambiguated trait-name listing
# --------------------------------------------------------------------------- #


def test_trait_names_lists_latest_and_excludes_superseded(pg_conn):
    with pg_conn.cursor() as cur:
        _, _, imgs = _seed_experiment_scan(cur)
        _deliver(
            cur, imgs, "old", traits=[_trait("only_old", 1.0), _trait("shared", 2.0)]
        )
        _deliver(
            cur, imgs, "new", traits=[_trait("shared", 3.0)]
        )  # latest lacks only_old
        cur.execute("SELECT name FROM cyl_scan_trait_names")
        names = {r[0] for r in cur.fetchall()}
        assert "shared" in names
        assert "only_old" not in names  # only in a superseded source
    pg_conn.rollback()


def test_trait_names_excludes_null(pg_conn):
    with pg_conn.cursor() as cur:
        _, scan_id, _ = _seed_experiment_scan(cur)
        # latest row with NULL trait_id -> NULL trait_name must not surface
        cur.execute(
            "INSERT INTO cyl_scan_traits (scan_id, source_id, trait_id, value) VALUES (%s, NULL, NULL, %s)",
            (scan_id, 1.0),
        )
        cur.execute("SELECT count(*) FROM cyl_scan_trait_names WHERE name IS NULL")
        assert cur.fetchone()[0] == 0
    pg_conn.rollback()


# --------------------------------------------------------------------------- #
# Requirement: Read path stays open to read roles with no RLS change
# --------------------------------------------------------------------------- #

_READ_ROLES = ["bloom_agent", "bloom_user", "bloom_admin"]


def test_read_roles_are_not_bypassrls(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT rolname, rolbypassrls FROM pg_roles WHERE rolname = ANY(%s)",
            (_READ_ROLES,),
        )
        assert all(b is False for _, b in cur.fetchall())
    pg_conn.rollback()


@pytest.mark.parametrize("role", _READ_ROLES)
def test_read_roles_can_use_read_surface(pg_conn, role):
    with pg_conn.cursor() as cur:
        exp, _, imgs = _seed_experiment_scan(cur)
        _deliver(cur, imgs, "k", run="r", traits=[_trait("length", 1.0)])
        cur.execute(f"SET LOCAL ROLE {role}")
        # joined source_name column must be readable under security_invoker
        cur.execute("SELECT source_name FROM cyl_scan_traits_source LIMIT 1")
        cur.fetchone()
        cur.execute("SELECT count(*) FROM cyl_scan_traits_latest")
        cur.execute("SELECT count(*) FROM cyl_scan_trait_names")
        # The call must be PERMITTED (no InsufficientPrivilege). Row count is a function of the
        # invoker's RLS context on the ancillary join tables (auth.uid()-based; 0 under a raw
        # SET LOCAL ROLE with no JWT) — unchanged by this read-path change.
        cur.execute("SELECT count(*) FROM get_scan_traits(%s, %s)", (exp, "length"))
        assert cur.fetchone()[0] is not None
        cur.execute("RESET ROLE")
    pg_conn.rollback()


def test_migration_adds_no_write_capability():
    # The read-path migration is read-only: it grants only SELECT and creates no policy, so it
    # cannot widen write access to any table (a static property of the migration text).
    sql = MIGRATION.read_text().lower()
    assert "create policy" not in sql
    for verb in ("grant insert", "grant update", "grant delete", "grant all"):
        assert verb not in sql, f"migration must not {verb}"
    for clause in ("for insert", "for update", "for delete"):
        assert clause not in sql, f"migration must not create a {clause} policy"


# --------------------------------------------------------------------------- #
# Requirement: Additive, non-destructive read-path migration
# --------------------------------------------------------------------------- #


def test_migration_body_is_idempotent(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(MIGRATION))  # re-apply on already-applied state
        for view in (
            "cyl_scan_traits_source",
            "cyl_scan_traits_latest",
            "cyl_scan_trait_names",
        ):
            cur.execute("SELECT to_regclass(%s)", (f"public.{view}",))
            assert cur.fetchone()[0] is not None
        cur.execute(
            "SELECT count(*) FROM pg_proc WHERE proname='get_scan_traits' AND pronargs=4"
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_rollback_restores_prior_read_surface(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(_sql_body(ROLLBACK))
        for view in ("cyl_scan_traits_source", "cyl_scan_traits_latest"):
            cur.execute("SELECT to_regclass(%s)", (f"public.{view}",))
            assert cur.fetchone()[0] is None, f"{view} should be dropped by rollback"
        # exactly one get_scan_traits remains, back to the 2-arg signature (no overload)
        cur.execute(
            "SELECT count(*), min(pronargs), max(pronargs) FROM pg_proc WHERE proname='get_scan_traits'"
        )
        assert cur.fetchone() == (1, 2, 2)
        # cyl_scan_trait_names back to the registry passthrough: a registered-but-unmeasured
        # name (which the latest-only view would exclude) is present again
        _register_trait(cur, "unmeasured_reg_name")
        cur.execute(
            "SELECT count(*) FROM cyl_scan_trait_names WHERE name='unmeasured_reg_name'"
        )
        assert cur.fetchone()[0] == 1
    pg_conn.rollback()


def test_recreated_trait_names_readable_by_read_role(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute("SET LOCAL ROLE bloom_agent")
        cur.execute("SELECT count(*) FROM cyl_scan_trait_names")
        assert cur.fetchone()[0] is not None
        cur.execute("RESET ROLE")
    pg_conn.rollback()
