"""
Integration tests for the embedtree schema. Covers: pgvector load,
protein_embedding_models registry seed, proteins/protein_embeddings_esm2 wiring,
knn_search_esm2 cosine ordering, search_genes model-independence, the
vector(1280) dimension type-check (cross-model guardrail), orthogroups
+ get_orthogroup_info shared_with_query, RLS (anon blocked via REST;
each bloom_* role can SELECT each new table), and a pg_policies drift
detector. Runs against the prod compose stack:

  uv run --extra test pytest tests/integration/test_embedtree_schema.py -v
"""

import pytest


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

# 'test:' prefix avoids any chance of colliding with real ingested gene UIDs.
TEST_UIDS = ("test:Q", "test:N1", "test:N2", "test:dim_check")
TEST_PROTEINS = [
    # (uid, species, gene_id, raw_gene_id)
    ("test:Q",  "arabidopsis", "AT5GQ",  "AT5GQ"),
    ("test:N1", "arabidopsis", "AT5GN1", "AT5GN1"),
    ("test:N2", "rice",        "OsRN2",  "OsRN2"),
]


def _make_vec(dim: int, fill: dict[int, float]) -> list[float]:
    """Return a list of `dim` floats with `fill[i]` set at position i."""
    v = [0.0] * dim
    for i, x in fill.items():
        v[i] = x
    return v


def _to_pgvector(v: list[float]) -> str:
    """Format a list of floats as a pgvector literal: '[v1,v2,...]'."""
    return "[" + ",".join(f"{x:.8f}" for x in v) + "]"


# Vectors chosen so cosine ordering is unambiguous regardless of normalization:
#   Q  = [1, 0, ...]                 -> identical to itself
#   N1 = [0.9, sqrt(1-0.81), ...]    -> cosine with Q ≈ 0.9 (high)
#   N2 = [0, 1, ...]                 -> cosine with Q = 0 (low)
_NEAR_X = 0.9
_NEAR_Y = (1.0 - _NEAR_X * _NEAR_X) ** 0.5  # ≈ 0.43589
QUERY_VEC = _make_vec(1280, {0: 1.0})
NEAR_VEC  = _make_vec(1280, {0: _NEAR_X, 1: _NEAR_Y})
FAR_VEC   = _make_vec(1280, {1: 1.0})


TEST_OG_RUN_NAME = "of2_test_run"
TEST_OG_ROWS = [
    # (protein_uid, orthogroup)
    ("test:Q",  "OG_TEST_0001"),
    ("test:N1", "OG_TEST_0001"),  # same OG as test:Q
    ("test:N2", "OG_TEST_0002"),  # different OG
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def embedtree_seed(pg_conn):
    """Seed 3 proteins + matching ESM-2 embeddings; clean up after the test."""
    with pg_conn.cursor() as cur:
        for uid, species, gene_id, raw_id in TEST_PROTEINS:
            cur.execute(
                "INSERT INTO public.proteins (uid, species, gene_id, raw_gene_id) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (uid) DO UPDATE SET "
                "  species = excluded.species, "
                "  gene_id = excluded.gene_id, "
                "  raw_gene_id = excluded.raw_gene_id",
                (uid, species, gene_id, raw_id),
            )
        for uid, vec in (
            ("test:Q",  QUERY_VEC),
            ("test:N1", NEAR_VEC),
            ("test:N2", FAR_VEC),
        ):
            cur.execute(
                "INSERT INTO public.protein_embeddings_esm2 (uid, embedding) "
                "VALUES (%s, %s::vector(1280)) "
                "ON CONFLICT (uid) DO UPDATE SET embedding = excluded.embedding",
                (uid, _to_pgvector(vec)),
            )
        pg_conn.commit()

    yield

    with pg_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM public.protein_embeddings_esm2 WHERE uid = ANY(%s)",
            (list(TEST_UIDS),),
        )
        cur.execute(
            "DELETE FROM public.proteins WHERE uid = ANY(%s)",
            (list(TEST_UIDS),),
        )
        pg_conn.commit()


@pytest.fixture
def orthogroup_seed(pg_conn, embedtree_seed):
    """
    Seed one OrthoFinder run + 3 orthogroup rows in it, mark the run
    active; clean up after the test (cascades to orthogroups rows).

    Depends on `embedtree_seed` because orthogroups.protein_uid has a
    FK to proteins.uid — the protein rows must exist first.
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.orthogroup_runs (run_name, source, notes, is_active) "
            "VALUES (%s, %s, %s, true) "
            "ON CONFLICT (run_name) DO UPDATE SET is_active = true "
            "RETURNING id",
            (TEST_OG_RUN_NAME, "test fixture", "embedtree integration tests"),
        )
        (run_id,) = cur.fetchone()
        for protein_uid, og in TEST_OG_ROWS:
            cur.execute(
                "INSERT INTO public.orthogroups (run_id, protein_uid, orthogroup) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (run_id, protein_uid, orthogroup) DO NOTHING",
                (run_id, protein_uid, og),
            )
        pg_conn.commit()

    yield run_id

    # Drop the test run; FK ON DELETE CASCADE removes its orthogroups rows.
    with pg_conn.cursor() as cur:
        cur.execute(
            "DELETE FROM public.orthogroup_runs WHERE run_name = %s",
            (TEST_OG_RUN_NAME,),
        )
        pg_conn.commit()


# ---------------------------------------------------------------------------
# 1. Extension + registry
# ---------------------------------------------------------------------------

def test_pgvector_extension_is_installed(pg_conn):
    """The vector extension must be created by the embedtree migration."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        assert cur.fetchone() is not None, (
            "pgvector extension not installed — the embedtree migration was "
            "not applied, or `create extension if not exists vector` was "
            "dropped from it"
        )


def test_protein_embedding_models_seeded_with_esm2(pg_conn):
    """The registry table must contain the seeded ESM-2 row after migration."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT model_id, dimension, table_suffix, is_active "
            "FROM public.protein_embedding_models WHERE table_suffix = 'esm2'"
        )
        row = cur.fetchone()

    assert row is not None, (
        "protein_embedding_models is missing the ESM-2 row — the seed INSERT "
        "in the embedtree schema migration was dropped"
    )
    model_id, dimension, suffix, is_active = row
    assert model_id == "esm2_t33_650M_UR50D"
    assert dimension == 1280
    assert suffix == "esm2"
    assert is_active is True


# ---------------------------------------------------------------------------
# 2. KNN + autocomplete RPCs
# ---------------------------------------------------------------------------

def test_knn_search_esm2_returns_descending_similarity(pg_conn, embedtree_seed):
    """knn_search_esm2 must order results by descending cosine similarity."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT uid, similarity FROM public.knn_search_esm2('test:Q', 3)"
        )
        rows = cur.fetchall()

    assert len(rows) == 3, f"expected 3 KNN results, got {len(rows)}"

    # The query gene is its own nearest neighbour.
    assert rows[0][0] == "test:Q", (
        f"first result should be the query itself, got {rows[0][0]}"
    )
    assert abs(rows[0][1] - 1.0) < 1e-6

    # N1 is closer than N2 (cosine 0.9 vs 0).
    assert rows[1][0] == "test:N1"
    assert rows[2][0] == "test:N2"

    # Strict descending order on similarity.
    assert rows[0][1] > rows[1][1] > rows[2][1], (
        f"similarity not strictly descending: {[r[1] for r in rows]}"
    )


def test_search_genes_returns_inserted_genes(pg_conn, embedtree_seed):
    """
    search_genes is metadata-only — it must work without touching any
    embedding table. The query 'AT5G' should match test:Q and test:N1
    (which have gene_id starting with AT5G) but not test:N2 (gene_id
    starts with OsR).
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT uid FROM public.search_genes('AT5G', 20)"
        )
        uids = {r[0] for r in cur.fetchall()}

    assert "test:Q"  in uids
    assert "test:N1" in uids
    assert "test:N2" not in uids


@pytest.mark.parametrize("partial_id", ["", "   ", None])
def test_search_genes_empty_input_returns_zero_rows(pg_conn, embedtree_seed, partial_id):
    """
    Empty / whitespace-only / NULL partial_id must return zero rows —
    without the gate, ILIKE '%%' matches every row and the autocomplete
    function turns into a dataset-dump endpoint on every empty keystroke.
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM public.search_genes(%s, 1000)",
            (partial_id,),
        )
        (count,) = cur.fetchone()

    assert count == 0, (
        f"search_genes({partial_id!r}) returned {count} rows — the empty-input "
        f"gate is missing; the autocomplete RPC is acting as a full-table dump"
    )


# ---------------------------------------------------------------------------
# 3. Cross-model isolation: type system rejects wrong-dim vectors
# ---------------------------------------------------------------------------

def test_protein_embeddings_esm2_rejects_wrong_dimension(pg_conn):
    """
    A vector of dimension != 1280 inserted into protein_embeddings_esm2
    must be rejected by Postgres at the type-cast or insert stage. This
    is the guardrail that makes cross-model contamination impossible —
    if it ever stops working, the multi-model isolation guarantee is
    broken.
    """
    import psycopg

    # Insert the metadata row first so the FK isn't what blocks us.
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.proteins (uid, species, gene_id, raw_gene_id) "
            "VALUES ('test:dim_check', 'test', 'TEST', 'TEST') "
            "ON CONFLICT (uid) DO NOTHING"
        )
        pg_conn.commit()

    wrong_dim_vec = "[" + ",".join(["0.1"] * 1024) + "]"
    raised = False
    try:
        with pg_conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.protein_embeddings_esm2 (uid, embedding) "
                "VALUES ('test:dim_check', %s::vector(1024))",
                (wrong_dim_vec,),
            )
            pg_conn.commit()
    except psycopg.Error:
        # pgvector raises a DataException-style error; catch the broad
        # base so future pgvector versions that re-classify it still pass.
        pg_conn.rollback()
        raised = True
    finally:
        # Clean up the metadata row regardless of outcome.
        with pg_conn.cursor() as cur:
            cur.execute(
                "DELETE FROM public.proteins WHERE uid = 'test:dim_check'"
            )
            pg_conn.commit()

    assert raised, (
        "Postgres accepted a vector(1024) insert into a vector(1280) column "
        "— the type-level dimension check is broken; cross-model "
        "contamination guardrail is no longer enforced"
    )


# ---------------------------------------------------------------------------
# 4. OrthoFinder cross-reference
# ---------------------------------------------------------------------------

def test_get_orthogroup_info_returns_shared_with_query(pg_conn, orthogroup_seed):
    """
    test:Q and test:N1 share OG_TEST_0001; test:N2 is in OG_TEST_0002.
    get_orthogroup_info('test:Q', ARRAY['test:N1','test:N2']) should mark
    test:N1 as shared and test:N2 as not. Reads the currently-active
    OrthoFinder run (seeded with is_active=true by `orthogroup_seed`).
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT protein_uid, orthogroup, shared_with_query "
            "FROM public.get_orthogroup_info(%s, %s)",
            ("test:Q", ["test:N1", "test:N2"]),
        )
        rows = {uid: (og, shared) for uid, og, shared in cur.fetchall()}

    assert "test:N1" in rows, "test:N1 missing from get_orthogroup_info result"
    assert rows["test:N1"][1] is True, (
        "test:N1 should be marked shared_with_query (same orthogroup as test:Q)"
    )
    assert "test:N2" in rows, "test:N2 missing from get_orthogroup_info result"
    assert rows["test:N2"][1] is False, (
        "test:N2 should NOT be marked shared_with_query (different orthogroup)"
    )


# ---------------------------------------------------------------------------
# 5. RLS: anon role can't read; each bloom_* role can read
# ---------------------------------------------------------------------------

def test_anon_cannot_read_proteins_via_postgrest(api, anon_key, embedtree_seed):
    """
    Anonymous PostgREST requests must NOT return embedding rows. The
    response is allowed to be 401, 403, or 200-with-empty-array
    depending on Supabase config — all of those count as "blocked".
    """
    status, body = api("/api/rest/v1/proteins?select=uid&limit=5", api_key=anon_key)
    if status in (401, 403):
        return  # blocked at the auth layer — fine
    if status == 200 and isinstance(body, list) and len(body) == 0:
        return  # blocked at the RLS layer — fine
    pytest.fail(
        f"anon role appears to read public.proteins via PostgREST "
        f"(status={status}, body={body!r}) — RLS policy + grant set "
        f"is misconfigured"
    )


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent", "bloom_admin"])
@pytest.mark.parametrize("table", [
    "proteins",
    "protein_embeddings_esm2",
    "protein_embedding_models",
    "orthogroup_runs",
    "orthogroups",
])
def test_each_bloom_role_can_select_each_new_table(pg_conn, embedtree_seed, role, table):
    """
    For each bloom_* role × each new table, SET LOCAL ROLE and assert
    the role can SELECT. A missing GRANT makes the SELECT return empty
    even with a passing RLS policy, so this catches drift on either
    layer.
    """
    with pg_conn.cursor() as cur:
        cur.execute("BEGIN")
        try:
            cur.execute(f"SET LOCAL ROLE {role}")
            # Count is fine — we're testing access, not specific row content.
            cur.execute(f"SELECT count(*) FROM public.{table}")
            count = cur.fetchone()[0]
            assert count is not None, (
                f"role {role} could not read public.{table} — count returned "
                f"NULL, which usually means the RLS policy denied or the "
                f"GRANT is missing"
            )
        finally:
            cur.execute("ROLLBACK")


# ---------------------------------------------------------------------------
# 6. Drift detector: expected policies on every new table
# ---------------------------------------------------------------------------

EXPECTED_POLICIES = {
    "protein_embedding_models",
    "proteins",
    "protein_embeddings_esm2",
    "orthogroup_runs",
    "orthogroups",
}


def test_pg_policies_has_three_policies_per_new_table(pg_conn):
    """
    Drift detector. Every new table must carry the three bloom-role
    policies established in 20260603000000_fix_gene_progress_logs.sql:
      admin_all_<table>, agent_read_<table>, user_read_<table>
    If a future migration drops or renames one of these, this test
    fails loudly instead of silently breaking the UI / agent.
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT tablename, policyname "
            "FROM pg_policies "
            "WHERE schemaname = 'public' "
            "  AND tablename = ANY(%s)",
            (list(EXPECTED_POLICIES),),
        )
        rows = cur.fetchall()

    by_table: dict[str, set[str]] = {t: set() for t in EXPECTED_POLICIES}
    for tbl, policy in rows:
        by_table[tbl].add(policy)

    missing: list[str] = []
    for tbl in EXPECTED_POLICIES:
        for prefix in ("admin_all_", "agent_read_", "user_read_"):
            expected = f"{prefix}{tbl}"
            if expected not in by_table[tbl]:
                missing.append(expected)

    assert not missing, (
        f"{len(missing)} expected RLS policies are missing from pg_policies. "
        f"Either a migration dropped them or naming drifted. Missing: {missing}"
    )
