"""
Integration tests for the Arabidopsis-accession ESM-3 layer. Covers: the esm3
registry row, protein_embedding_runs provenance, arabidopsis_accessions
(natural key + one-reference + NOT NULLs), proteins.accession_id (FK, one
locus per accession, cross-species rows stay NULL), protein_embeddings_esm3
(vector(1536) dimension guardrail, zero-vector CHECK, run FK, ON DELETE
CASCADE, HNSW index / no ivfflat), the comparison RPCs (knn_search_esm3,
compare_gene_across_accessions, best_match_per_accession, and search_accession_genes
incl. accession scoping + empty-query browse) and the search_genes
scoping change, plus RLS (anon blocked via REST; read matrix for every bloom_*
role; write-denial for user/agent; writer can ingest) and a six-policy drift
detector.

  uv run --extra test pytest tests/integration/test_accession_esm3_schema.py -v
"""

import pytest


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

# 'atest:' prefix avoids colliding with real ingested UIDs or the embedtree
# test's 'test:' UIDs.
REF_UID = "atest:1:AT1G01010"      # Col-0 variant  (reference accession)
ALT_UID = "atest:2:AT1G01010"      # Ler-0 variant  (same gene, other accession)
SOLO_UID = "atest:2:AT2G00001"     # gene present in only one accession
XSPEC_UID = "atest:xspec:AT1G01010"  # cross-species protein, accession_id NULL
ACCESSION_UIDS = (REF_UID, ALT_UID, SOLO_UID)
ALL_UIDS = (*ACCESSION_UIDS, XSPEC_UID)

COL0_SRC, COL0_EXT = "1001 Genomes", "atest-6909"
LER0_SRC, LER0_EXT = "1001 Genomes", "atest-6932"

ESM3_DIM = 1536


def _make_vec(dim: int, fill: dict[int, float]) -> list[float]:
    v = [0.0] * dim
    for i, x in fill.items():
        v[i] = x
    return v


def _to_pgvector(v: list[float]) -> str:
    return "[" + ",".join(f"{x:.8f}" for x in v) + "]"


# Q identical to itself; ALT cosine≈0.9 vs Q; SOLO orthogonal (cosine 0).
_NEAR_X = 0.9
_NEAR_Y = (1.0 - _NEAR_X * _NEAR_X) ** 0.5
REF_VEC = _make_vec(ESM3_DIM, {0: 1.0})
ALT_VEC = _make_vec(ESM3_DIM, {0: _NEAR_X, 1: _NEAR_Y})
SOLO_VEC = _make_vec(ESM3_DIM, {1: 1.0})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def accession_seed(pg_conn):
    """
    Seed 2 accessions (Col-0 is_reference, Ler-0), one embedding run, three
    accession proteins (AT1G01010 in both accessions, AT2G00001 in Ler-0 only),
    a cross-species protein sharing the AT1G01010 gene_id, and ESM-3 embeddings
    for the three accession proteins. Teardown is reverse-FK order.
    """
    with pg_conn.cursor() as cur:
        cur.execute(
            "INSERT INTO public.arabidopsis_accessions "
            "  (common_name, external_id, id_source, is_reference) "
            "VALUES (%s, %s, %s, true) RETURNING id",
            ("atest-Col-0", COL0_EXT, COL0_SRC),
        )
        (col0_id,) = cur.fetchone()
        cur.execute(
            "INSERT INTO public.arabidopsis_accessions "
            "  (common_name, external_id, id_source, is_reference) "
            "VALUES (%s, %s, %s, false) RETURNING id",
            ("atest-Ler-0", LER0_EXT, LER0_SRC),
        )
        (ler0_id,) = cur.fetchone()

        cur.execute(
            "INSERT INTO public.protein_embedding_runs "
            "  (model_id, checkpoint_hash, pooling, layer, sequence_source, software_version) "
            "VALUES ('esm3_open_small_1p4B', %s, 'mean', 36, %s, 'atest-1.0') RETURNING id",
            ("atest-checkpoint-hash", "Araport11 primary transcript"),
        )
        (run_id,) = cur.fetchone()

        proteins = [
            (REF_UID, "arabidopsis", "AT1G01010", col0_id),
            (ALT_UID, "arabidopsis", "AT1G01010", ler0_id),
            (SOLO_UID, "arabidopsis", "AT2G00001", ler0_id),
            (XSPEC_UID, "arabidopsis", "AT1G01010", None),
        ]
        for uid, species, gene_id, accession_id in proteins:
            cur.execute(
                "INSERT INTO public.proteins (uid, species, gene_id, accession_id) "
                "VALUES (%s, %s, %s, %s)",
                (uid, species, gene_id, accession_id),
            )
        for uid, vec in ((REF_UID, REF_VEC), (ALT_UID, ALT_VEC), (SOLO_UID, SOLO_VEC)):
            cur.execute(
                "INSERT INTO public.protein_embeddings_esm3 (uid, embedding, run_id) "
                "VALUES (%s, %s::vector(1536), %s)",
                (uid, _to_pgvector(vec), run_id),
            )
        pg_conn.commit()

    yield {"col0_id": col0_id, "ler0_id": ler0_id, "run_id": run_id}

    with pg_conn.cursor() as cur:
        cur.execute("DELETE FROM public.protein_embeddings_esm3 WHERE uid = ANY(%s)", (list(ALL_UIDS),))
        cur.execute("DELETE FROM public.proteins WHERE uid = ANY(%s)", (list(ALL_UIDS),))
        cur.execute(
            "DELETE FROM public.protein_embedding_runs WHERE checkpoint_hash = %s",
            ("atest-checkpoint-hash",),
        )
        cur.execute(
            "DELETE FROM public.arabidopsis_accessions WHERE external_id = ANY(%s)",
            ([COL0_EXT, LER0_EXT],),
        )
        pg_conn.commit()


def _expect_violation(pg_conn, sql, params=None, sqlstate_prefix=None):
    """Run sql inside a savepoint; assert it raises, roll back to the savepoint."""
    import psycopg

    with pg_conn.cursor() as cur:
        cur.execute("SAVEPOINT sp")
        raised = None
        try:
            cur.execute(sql, params or ())
        except psycopg.Error as exc:
            raised = exc
        cur.execute("ROLLBACK TO SAVEPOINT sp")
    assert raised is not None, f"expected a violation but the statement succeeded: {sql}"
    if sqlstate_prefix:
        assert (raised.sqlstate or "").startswith(sqlstate_prefix), (
            f"expected SQLSTATE {sqlstate_prefix}*, got {raised.sqlstate}: {raised}"
        )


# ---------------------------------------------------------------------------
# 1. Registry + provenance
# ---------------------------------------------------------------------------

def test_esm3_model_registered(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT dimension, table_suffix FROM public.protein_embedding_models "
            "WHERE model_id = 'esm3_open_small_1p4B'"
        )
        row = cur.fetchone()
    assert row is not None, "esm3 model row missing from protein_embedding_models"
    assert row[0] == 1536
    assert row[1] == "esm3"


def test_embedding_run_requires_provenance(pg_conn):
    """model_id/checkpoint_hash/pooling/sequence_source are NOT NULL."""
    _expect_violation(
        pg_conn,
        "INSERT INTO public.protein_embedding_runs (model_id, checkpoint_hash, pooling, sequence_source) "
        "VALUES ('esm3_open_small_1p4B', NULL, 'mean', 'src')",
        sqlstate_prefix="23",
    )


def test_embedding_run_id_resolves(pg_conn, accession_seed):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT r.pooling, r.checkpoint_hash, r.sequence_source "
            "FROM public.protein_embeddings_esm3 e "
            "JOIN public.protein_embedding_runs r ON r.id = e.run_id "
            "WHERE e.uid = %s",
            (REF_UID,),
        )
        row = cur.fetchone()
    assert row == ("mean", "atest-checkpoint-hash", "Araport11 primary transcript")


# ---------------------------------------------------------------------------
# 2. arabidopsis_accessions
# ---------------------------------------------------------------------------

def test_accession_natural_key_and_reference(pg_conn, accession_seed):
    # Same external_id, different source → allowed.
    with pg_conn.cursor() as cur:
        cur.execute("SAVEPOINT sp")
        cur.execute(
            "INSERT INTO public.arabidopsis_accessions (common_name, external_id, id_source) "
            "VALUES ('atest-other', %s, 'TAIR')",
            (COL0_EXT,),
        )
        cur.execute("ROLLBACK TO SAVEPOINT sp")

    # Duplicate (id_source, external_id) → rejected.
    _expect_violation(
        pg_conn,
        "INSERT INTO public.arabidopsis_accessions (common_name, external_id, id_source) "
        "VALUES ('atest-dup', %s, %s)",
        (COL0_EXT, COL0_SRC),
        sqlstate_prefix="23",
    )
    # A second is_reference=true → rejected (partial unique index).
    _expect_violation(
        pg_conn,
        "UPDATE public.arabidopsis_accessions SET is_reference = true WHERE external_id = %s",
        (LER0_EXT,),
        sqlstate_prefix="23",
    )
    # NULL required column → rejected.
    _expect_violation(
        pg_conn,
        "INSERT INTO public.arabidopsis_accessions (common_name, external_id, id_source) "
        "VALUES (NULL, 'x', 'y')",
        sqlstate_prefix="23",
    )


# ---------------------------------------------------------------------------
# 3. proteins.accession_id
# ---------------------------------------------------------------------------

def test_accession_id_fk_and_uniqueness(pg_conn, accession_seed):
    col0_id = accession_seed["col0_id"]

    # FK: unknown accession_id rejected.
    _expect_violation(
        pg_conn,
        "INSERT INTO public.proteins (uid, gene_id, accession_id) VALUES ('atest:bad', 'G', 2147483000)",
        sqlstate_prefix="23",
    )
    # Same gene across accessions → distinct uids already seeded; assert both exist.
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT count(DISTINCT uid) FROM public.proteins "
            "WHERE gene_id = 'AT1G01010' AND accession_id IS NOT NULL"
        )
        assert cur.fetchone()[0] == 2
    # One locus per accession: duplicate (accession_id, gene_id) rejected.
    _expect_violation(
        pg_conn,
        "INSERT INTO public.proteins (uid, gene_id, accession_id) VALUES ('atest:dup', 'AT1G01010', %s)",
        (col0_id,),
        sqlstate_prefix="23",
    )
    # Cross-species rows (accession_id NULL) may repeat a gene_id.
    with pg_conn.cursor() as cur:
        cur.execute("SAVEPOINT sp")
        cur.execute(
            "INSERT INTO public.proteins (uid, gene_id, accession_id) VALUES ('atest:xspec2', 'AT1G01010', NULL)"
        )
        cur.execute("ROLLBACK TO SAVEPOINT sp")


# ---------------------------------------------------------------------------
# 4. protein_embeddings_esm3 guardrails
# ---------------------------------------------------------------------------

def test_embedding_dimension_guardrail(pg_conn, accession_seed):
    _expect_violation(
        pg_conn,
        "INSERT INTO public.protein_embeddings_esm3 (uid, embedding, run_id) "
        "VALUES (%s, '[1,0,0]'::vector(3), %s)",
        (SOLO_UID, accession_seed["run_id"]),
    )


def test_embedding_zero_vector_rejected(pg_conn, accession_seed):
    """The non-zero CHECK (not the FK) rejects an all-zeros vector. Uses a
    fresh, valid protein uid so the only constraint that can fire is the CHECK
    — asserting the exact SQLSTATE 23514 so removing the CHECK is detectable."""
    import psycopg

    zero = _to_pgvector(_make_vec(ESM3_DIM, {}))
    with pg_conn.cursor() as cur:
        cur.execute("SAVEPOINT sp")
        cur.execute(
            "INSERT INTO public.proteins (uid, gene_id, accession_id) "
            "VALUES ('atest:zerop', 'ATZERO', %s)",
            (accession_seed["col0_id"],),
        )
        raised = None
        try:
            cur.execute(
                "INSERT INTO public.protein_embeddings_esm3 (uid, embedding, run_id) "
                "VALUES ('atest:zerop', %s::vector(1536), %s)",
                (zero, accession_seed["run_id"]),
            )
        except psycopg.Error as exc:
            raised = exc
        cur.execute("ROLLBACK TO SAVEPOINT sp")
    assert raised is not None, "zero vector was accepted"
    assert raised.sqlstate == "23514", (
        f"expected check_violation 23514 (the non-zero CHECK), got "
        f"{raised.sqlstate}: {raised}"
    )


def test_embedding_run_fk_enforced(pg_conn, accession_seed):
    vec = _to_pgvector(_make_vec(ESM3_DIM, {0: 1.0}))
    # Need a fresh protein uid so the PK doesn't fire first.
    with pg_conn.cursor() as cur:
        cur.execute("SAVEPOINT outer_sp")
        cur.execute(
            "INSERT INTO public.proteins (uid, gene_id, accession_id) VALUES ('atest:frfk', 'AT3G0', %s)",
            (accession_seed["ler0_id"],),
        )
        raised = None
        import psycopg
        try:
            cur.execute(
                "INSERT INTO public.protein_embeddings_esm3 (uid, embedding, run_id) "
                "VALUES ('atest:frfk', %s::vector(1536), 2147483000)",
                (vec,),
            )
        except psycopg.Error as exc:
            raised = exc
        cur.execute("ROLLBACK TO SAVEPOINT outer_sp")
    assert raised is not None and (raised.sqlstate or "").startswith("23")


def test_embedding_cascades_on_protein_delete(pg_conn, accession_seed):
    with pg_conn.cursor() as cur:
        cur.execute("SAVEPOINT sp")
        cur.execute("DELETE FROM public.proteins WHERE uid = %s", (SOLO_UID,))
        cur.execute("SELECT count(*) FROM public.protein_embeddings_esm3 WHERE uid = %s", (SOLO_UID,))
        remaining = cur.fetchone()[0]
        cur.execute("ROLLBACK TO SAVEPOINT sp")
    assert remaining == 0, "ON DELETE CASCADE did not remove the embedding"


def test_hnsw_index_present_no_ivfflat(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname='public' AND tablename='protein_embeddings_esm3'"
        )
        defs = [r[0].lower() for r in cur.fetchall()]
    assert any("hnsw" in d and "vector_cosine_ops" in d for d in defs), "HNSW cosine index missing"
    assert not any("ivfflat" in d for d in defs), "ivfflat index must not be used"


# ---------------------------------------------------------------------------
# 5. RPCs
# ---------------------------------------------------------------------------

def test_knn_search_esm3_excludes_self_and_orders(pg_conn, accession_seed):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT uid, accession_name, gene_id, similarity "
            "FROM public.knn_search_esm3(%s, 10)",
            (REF_UID,),
        )
        rows = cur.fetchall()
    uids = [r[0] for r in rows]
    # Approximate (HNSW) contract: the query itself is excluded, results are
    # accession-scoped and nearest-first, and the clear nearest (ALT, cosine ~0.9)
    # is found and ranked first. Complete recall of the far, orthogonal SOLO
    # (cosine ~0) is NOT asserted — an HNSW index does not guarantee the farthest
    # neighbour, and this surface is exploratory ("predicted, not verified").
    assert REF_UID not in uids  # self-exclusion: query is never returned
    assert XSPEC_UID not in uids  # accession-only scope
    assert ALT_UID in uids  # the clear nearest neighbour is found
    assert uids[0] == ALT_UID  # nearest-first ordering
    assert rows[0][1] == "atest-Ler-0"  # ALT's accession_name
    sims = [r[3] for r in rows]
    assert sims == sorted(sims, reverse=True)  # non-increasing similarity
    if len(rows) >= 2:
        # Strict: nearest is more similar than the farthest returned — catches a
        # constant/broken similarity (a constant list is trivially "sorted").
        assert rows[0][3] > rows[-1][3]


def test_knn_search_esm3_match_count_is_neighbor_count(pg_conn, accession_seed):
    """match_count = number of NEIGHBORS returned (query excluded), so a
    request for K yields K real neighbors, not K-1."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT uid FROM public.knn_search_esm3(%s, 1)", (REF_UID,))
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == ALT_UID  # the single nearest neighbor, not self


def test_knn_search_esm3_missing_query_returns_empty(pg_conn, accession_seed):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.knn_search_esm3('atest:nope', 10)")
        assert cur.fetchone()[0] == 0


def test_compare_default_reference_is_col0(pg_conn, accession_seed):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT uid, is_reference, similarity "
            "FROM public.compare_gene_across_accessions('AT1G01010')"
        )
        rows = cur.fetchall()
    assert rows[0][0] == REF_UID and rows[0][1] is True
    assert abs(rows[0][2] - 1.0) < 1e-6
    assert {r[0] for r in rows} == {REF_UID, ALT_UID}  # accession variants only


def test_compare_explicit_reference(pg_conn, accession_seed):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT uid, is_reference FROM public.compare_gene_across_accessions('AT1G01010', %s)",
            (ALT_UID,),
        )
        rows = cur.fetchall()
    assert rows[0][0] == ALT_UID and rows[0][1] is True


def test_compare_reference_from_other_gene_returns_empty(pg_conn, accession_seed):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM public.compare_gene_across_accessions('AT1G01010', %s)",
            (SOLO_UID,),  # SOLO_UID's gene is AT2G00001, not AT1G01010
        )
        assert cur.fetchone()[0] == 0


def test_compare_single_accession_gene(pg_conn, accession_seed):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT uid, is_reference, similarity "
            "FROM public.compare_gene_across_accessions('AT2G00001')"
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == SOLO_UID and rows[0][1] is True and abs(rows[0][2] - 1.0) < 1e-6


def test_compare_unknown_gene_returns_empty(pg_conn, accession_seed):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.compare_gene_across_accessions('AT9G99999')")
        assert cur.fetchone()[0] == 0


def test_search_accession_genes_scope(pg_conn, accession_seed):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT uid FROM public.search_accession_genes('AT1G01010', 20)")
        uids = {r[0] for r in cur.fetchall()}
    assert uids == {REF_UID, ALT_UID}  # accession-only; excludes XSPEC_UID


def test_search_accession_genes_empty_input(pg_conn, accession_seed):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.search_accession_genes('   ', 20)")
        assert cur.fetchone()[0] == 0


def test_search_genes_excludes_accession_proteins(pg_conn, accession_seed):
    """The cross-species picker must return only the accession_id-NULL protein."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT uid FROM public.search_genes('AT1G01010', 20)")
        uids = {r[0] for r in cur.fetchall()}
    assert XSPEC_UID in uids
    assert not (uids & set(ACCESSION_UIDS)), f"accession proteins leaked into search_genes: {uids}"


def test_search_accession_genes_scoped_to_one_accession(pg_conn, accession_seed):
    """filter_accession_id restricts suggestions to a single accession's genes."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT uid FROM public.search_accession_genes('AT1G01010', 20, %s)",
            (accession_seed["col0_id"],),
        )
        uids = {r[0] for r in cur.fetchall()}
    assert uids == {REF_UID}  # Col-0's variant only — Ler-0's ALT_UID excluded


def test_search_accession_genes_empty_lists_scoped_accession(pg_conn, accession_seed):
    """Empty query lists the scoped accession's genes (browse without knowing the
    ID format); empty with no accession filter still returns nothing."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT uid FROM public.search_accession_genes('', 20, %s)",
            (accession_seed["ler0_id"],),
        )
        assert {r[0] for r in cur.fetchall()} == {ALT_UID, SOLO_UID}  # both Ler-0 genes
        cur.execute("SELECT count(*) FROM public.search_accession_genes('', 20, NULL)")
        assert cur.fetchone()[0] == 0  # empty + unscoped lists nothing (never all accessions)


# ---------------------------------------------------------------------------
# 5b. best_match_per_accession (embedding comparison across accessions)
# ---------------------------------------------------------------------------

def test_best_match_per_accession_picks_best_in_each_other_accession(pg_conn, accession_seed):
    """Default (per_accession=1): one row per OTHER accession, each that
    accession's single closest protein by ESM-3 cosine. Query = Col-0's
    AT1G01010; the only other accession is Ler-0, whose closest is ALT (~0.9),
    not SOLO (~0)."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT accession_id, accession_name, uid, gene_id, similarity, rank_in_accession "
            "FROM public.best_match_per_accession(%s)",
            (REF_UID,),
        )
        rows = cur.fetchall()
    assert len(rows) == 1, f"expected one row (Ler-0 only), got {rows}"
    accession_id, accession_name, uid, gene_id, similarity, rank_in_acc = rows[0]
    assert accession_id == accession_seed["ler0_id"]
    assert accession_name == "atest-Ler-0"
    assert uid == ALT_UID  # ALT (~0.9) beats SOLO (~0) within Ler-0
    assert gene_id == "AT1G01010"
    assert abs(similarity - _NEAR_X) < 0.02  # cosine ≈ 0.9
    assert rank_in_acc == 1


def test_best_match_top_k_per_accession(pg_conn, accession_seed):
    """per_accession=2 returns Ler-0's two nearest to the query (ALT then SOLO),
    with rank_in_accession 1 and 2, ordered most-similar-first."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT uid, rank_in_accession, similarity "
            "FROM public.best_match_per_accession(%s, 2)",
            (REF_UID,),
        )
        rows = cur.fetchall()
    assert [r[0] for r in rows] == [ALT_UID, SOLO_UID]  # nearest first
    assert [r[1] for r in rows] == [1, 2]  # within-accession rank
    assert rows[0][2] > rows[1][2]  # ALT more similar than SOLO


def test_best_match_excludes_query_own_accession(pg_conn, accession_seed):
    """'Best match in OTHER accessions' — the query's own accession never appears."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT accession_id, uid FROM public.best_match_per_accession(%s)",
            (REF_UID,),
        )
        rows = cur.fetchall()
    assert all(r[0] != accession_seed["col0_id"] for r in rows), "query's own accession leaked in"
    assert REF_UID not in [r[1] for r in rows]


def test_best_match_missing_query_returns_empty(pg_conn, accession_seed):
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.best_match_per_accession('atest:nope')")
        assert cur.fetchone()[0] == 0


def test_best_match_bounds_do_not_crash(pg_conn, accession_seed):
    """per_accession clamps to >=1 (negative) and <=25 (huge); match_count
    clamps to LIMIT >=1 (no 'LIMIT -n' error)."""
    with pg_conn.cursor() as cur:
        # per_accession = -5 -> clamped to 1 -> Ler-0's single best
        cur.execute("SELECT count(*) FROM public.best_match_per_accession(%s, -5)", (REF_UID,))
        assert cur.fetchone()[0] == 1
        # per_accession huge -> clamped to 25 -> Ler-0 has 2 proteins in the pool
        cur.execute("SELECT count(*) FROM public.best_match_per_accession(%s, 100000)", (REF_UID,))
        assert cur.fetchone()[0] == 2
        # match_count = -1 -> clamped to LIMIT 1
        cur.execute("SELECT count(*) FROM public.best_match_per_accession(%s, 2, -1)", (REF_UID,))
        assert cur.fetchone()[0] == 1


def test_best_match_partitions_across_multiple_accessions(pg_conn, accession_seed):
    """The result must be grouped BY accession: with more than one OTHER
    accession, each contributes its OWN nearest protein(s), not the globally
    nearest. The 2-accession fixture can't show this (one partition == global),
    so seed a 3rd accession in a savepoint and verify the partitioning.

    Query = Col-0's REF (e0). Ler-0 has ALT(cos .9)/SOLO(cos 0); Bur-0 gets
    BURA(cos .95)/BURB(cos .5). Globally the two nearest are BURA then ALT — a
    global top-1 would return only Bur-0. Correct per-accession grouping returns
    Ler-0's ALT AND Bur-0's BURA, each rank 1."""
    bura_x, burb_x = 0.95, 0.5
    bura = _make_vec(ESM3_DIM, {0: bura_x, 1: (1 - bura_x * bura_x) ** 0.5})
    burb = _make_vec(ESM3_DIM, {0: burb_x, 1: (1 - burb_x * burb_x) ** 0.5})
    with pg_conn.cursor() as cur:
        cur.execute("SAVEPOINT sp3")
        cur.execute(
            "INSERT INTO public.arabidopsis_accessions (common_name, external_id, id_source) "
            "VALUES ('atest-Bur-0', 'atest-bur', '1001 Genomes') RETURNING id"
        )
        bur_id = cur.fetchone()[0]
        for uid, gene, vec in (
            ("atest:bur:G1", "ATBUR1", bura),
            ("atest:bur:G2", "ATBUR2", burb),
        ):
            cur.execute(
                "INSERT INTO public.proteins (uid, species, gene_id, accession_id) "
                "VALUES (%s, 'arabidopsis', %s, %s)",
                (uid, gene, bur_id),
            )
            cur.execute(
                "INSERT INTO public.protein_embeddings_esm3 (uid, embedding, run_id) "
                "VALUES (%s, %s::vector(1536), %s)",
                (uid, _to_pgvector(vec), accession_seed["run_id"]),
            )

        # per_accession = 1: BOTH other accessions appear, each its own best.
        cur.execute(
            "SELECT accession_id, uid, rank_in_accession "
            "FROM public.best_match_per_accession(%s, 1)",
            (REF_UID,),
        )
        by_acc = {r[0]: r for r in cur.fetchall()}
        assert accession_seed["ler0_id"] in by_acc, "Ler-0 dropped — not grouped per accession"
        assert bur_id in by_acc, "Bur-0 dropped — not grouped per accession"
        assert by_acc[accession_seed["ler0_id"]][1] == ALT_UID  # Ler-0's own nearest
        assert by_acc[bur_id][1] == "atest:bur:G1"              # Bur-0's own nearest, not global
        assert all(r[2] == 1 for r in by_acc.values())

        # per_accession = 2: Bur-0 shows both its proteins, ranked within itself.
        cur.execute(
            "SELECT uid, rank_in_accession FROM public.best_match_per_accession(%s, 2) "
            "WHERE accession_id = %s ORDER BY rank_in_accession",
            (REF_UID, bur_id),
        )
        bur_rows = cur.fetchall()
        assert [r[0] for r in bur_rows] == ["atest:bur:G1", "atest:bur:G2"]
        assert [r[1] for r in bur_rows] == [1, 2]

        cur.execute("ROLLBACK TO SAVEPOINT sp3")  # undo the 3rd accession


# ---------------------------------------------------------------------------
# 6. RLS: anon blocked, read matrix, write-denial, drift
# ---------------------------------------------------------------------------

NEW_TABLES = ["arabidopsis_accessions", "protein_embedding_runs", "protein_embeddings_esm3"]


@pytest.mark.parametrize("table", NEW_TABLES)
def test_anon_cannot_read_new_tables_via_postgrest(api, anon_key, accession_seed, table):
    status, body = api(f"/api/rest/v1/{table}?select=*&limit=5", api_key=anon_key)
    if status in (401, 403):
        return
    if status == 200 and isinstance(body, list) and len(body) == 0:
        return
    pytest.fail(f"anon role appears to read public.{table} (status={status}, body={body!r})")


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent", "bloom_admin", "bloom_writer"])
@pytest.mark.parametrize("table", NEW_TABLES)
def test_each_role_can_select(pg_conn, accession_seed, role, table):
    with pg_conn.cursor() as cur:
        cur.execute("BEGIN")
        try:
            cur.execute(f"SET LOCAL ROLE {role}")
            cur.execute(f"SELECT count(*) FROM public.{table}")
            assert cur.fetchone()[0] is not None
        finally:
            cur.execute("ROLLBACK")


@pytest.mark.parametrize("role", ["bloom_user", "bloom_agent"])
def test_read_only_roles_cannot_insert(pg_conn, accession_seed, role):
    import psycopg

    with pg_conn.cursor() as cur:
        cur.execute("BEGIN")
        try:
            cur.execute(f"SET LOCAL ROLE {role}")
            raised = None
            try:
                cur.execute(
                    "INSERT INTO public.arabidopsis_accessions (common_name, external_id, id_source) "
                    "VALUES ('atest-denied', 'atest-x', 'atest-src')"
                )
            except psycopg.Error as exc:
                raised = exc
            assert raised is not None, f"{role} was able to INSERT — write path is not denied"
            assert (raised.sqlstate or "").startswith("42"), (
                f"expected insufficient_privilege (42501), got {raised.sqlstate}"
            )
        finally:
            cur.execute("ROLLBACK")


def test_writer_role_can_ingest(pg_conn):
    """bloom_writer can insert an accession, a run, a protein, and an ESM-3
    embedding — the full ingest path, including the one table with the vector
    CHECK and a writer_insert policy."""
    vec = _to_pgvector(_make_vec(ESM3_DIM, {0: 1.0}))
    with pg_conn.cursor() as cur:
        cur.execute("BEGIN")
        try:
            cur.execute("SET LOCAL ROLE bloom_writer")
            cur.execute(
                "INSERT INTO public.arabidopsis_accessions (common_name, external_id, id_source) "
                "VALUES ('atest-writer', 'atest-w', 'atest-wsrc') RETURNING id"
            )
            acc_id = cur.fetchone()[0]
            assert acc_id is not None
            cur.execute(
                "INSERT INTO public.protein_embedding_runs "
                "  (model_id, checkpoint_hash, pooling, sequence_source) "
                "VALUES ('esm3_open_small_1p4B', 'atest-wh', 'mean', 'src') RETURNING id"
            )
            run_id = cur.fetchone()[0]
            assert run_id is not None
            cur.execute(
                "INSERT INTO public.proteins (uid, gene_id, accession_id) "
                "VALUES ('atest:writer:G', 'GW', %s)",
                (acc_id,),
            )
            cur.execute(
                "INSERT INTO public.protein_embeddings_esm3 (uid, embedding, run_id) "
                "VALUES ('atest:writer:G', %s::vector(1536), %s)",
                (vec, run_id),
            )  # succeeds → writer can ingest embeddings
        finally:
            cur.execute("ROLLBACK")  # never persist writer test rows


def test_embedding_run_model_id_fk(pg_conn):
    """protein_embedding_runs.model_id FKs to protein_embedding_models — a run
    naming an unregistered model is rejected."""
    _expect_violation(
        pg_conn,
        "INSERT INTO public.protein_embedding_runs "
        "  (model_id, checkpoint_hash, pooling, sequence_source) "
        "VALUES ('no_such_model', 'h', 'mean', 'src')",
        sqlstate_prefix="23",  # 23503 foreign_key_violation
    )


def test_rpc_match_count_bounds_do_not_crash(pg_conn, accession_seed):
    """Negative match_count clamps to >=1 (no LIMIT -n error); huge caps at 1000."""
    with pg_conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM public.knn_search_esm3(%s, -5)", (REF_UID,))
        assert cur.fetchone()[0] >= 1  # clamped, no runtime error
        cur.execute("SELECT count(*) FROM public.knn_search_esm3(%s, 100000)", (REF_UID,))
        assert cur.fetchone()[0] <= 1000
        cur.execute(
            "SELECT count(*) FROM public.compare_gene_across_accessions('AT1G01010', NULL, -1)"
        )
        assert cur.fetchone()[0] >= 1


def test_compare_reference_survives_truncation(pg_conn, accession_seed):
    """AT1G01010 has 2 accession variants; match_count=1 must still return the
    reference (it sorts first), not merely the most-similar non-reference."""
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT uid, is_reference "
            "FROM public.compare_gene_across_accessions('AT1G01010', NULL, 1)"
        )
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0][0] == REF_UID and rows[0][1] is True


def test_compare_empty_gene_returns_empty(pg_conn, accession_seed):
    with pg_conn.cursor() as cur:
        for g in ("", "   "):
            cur.execute(
                "SELECT count(*) FROM public.compare_gene_across_accessions(%s)", (g,)
            )
            assert cur.fetchone()[0] == 0, f"empty gene {g!r} should return no rows"


EXPECTED_POLICY_PREFIXES = (
    "admin_all_", "agent_read_", "user_read_",
    "writer_read_", "writer_insert_", "writer_update_",
)


def test_pg_policies_six_policy_pattern(pg_conn):
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT tablename, policyname FROM pg_policies "
            "WHERE schemaname='public' AND tablename = ANY(%s)",
            (NEW_TABLES,),
        )
        rows = cur.fetchall()
    by_table: dict[str, set[str]] = {t: set() for t in NEW_TABLES}
    for tbl, policy in rows:
        by_table[tbl].add(policy)
    missing = [
        f"{prefix}{tbl}"
        for tbl in NEW_TABLES
        for prefix in EXPECTED_POLICY_PREFIXES
        if f"{prefix}{tbl}" not in by_table[tbl]
    ]
    assert not missing, f"missing RLS policies (drift): {missing}"
