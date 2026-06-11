-- =============================================================================
-- 20260610000100_create_orthogroups.sql
--
-- Adds the OrthoFinder cross-reference layer for the embedtree feature.
-- Versioned-runs pattern: one row per OrthoFinder run lives in
-- `orthogroup_runs`, every (protein_uid -> orthogroup) mapping lives in
-- `orthogroups` scoped to a run_id, and exactly one run can be `is_active`
-- at a time (enforced by a partial unique index).
--
-- Why two tables instead of one:
--   - Re-runs are common (new species added, OrthoFinder version bump,
--     algorithm tweak). Each run is inserted as its own batch and can be
--     compared against any previous run via
--     `WHERE run_id = A EXCEPT WHERE run_id = B`.
--   - Activating a new run is one UPDATE on `orthogroup_runs`; readers
--     query the active run automatically via `get_orthogroup_info`.
--   - Cleanup is one `DELETE FROM orthogroup_runs WHERE id = ?` which
--     cascades to every row from that run in `orthogroups`.
--
-- Identifier space:
--   `orthogroups.protein_uid` REFERENCES `proteins(uid)` with ON DELETE
--   CASCADE. This forces ingest to map OrthoFinder's per-protein /
--   per-transcript IDs to `proteins.uid` before insert — eliminating
--   the silent identifier-mismatch failure mode where a gene-level
--   query gene_id can't join to an isoform-level OrthoFinder row.
--   If a future caller needs gene-level semantics, aggregate at query
--   time via `proteins.gene_id`.
--
-- Sparse by design: a protein with no OrthoFinder mapping simply has
-- no row in `orthogroups` for the active run. That's a normal state,
-- not an error.
--
-- RLS pattern: same admin_all / agent_read / user_read as the embedtree
-- schema (20260610000000_create_embedtree_schema.sql).
--
-- Idempotent: CREATE TABLE IF NOT EXISTS, DROP POLICY IF EXISTS,
-- DROP FUNCTION IF EXISTS.
-- =============================================================================

BEGIN;

-- ─── orthogroup_runs: one row per OrthoFinder run ─────────────────────────
CREATE TABLE IF NOT EXISTS public.orthogroup_runs (
  id          bigserial   PRIMARY KEY,
  run_name    text        NOT NULL UNIQUE,
  source      text,
  notes       text,
  is_active   boolean     NOT NULL DEFAULT false,
  computed_at timestamptz NOT NULL DEFAULT now()
);

-- At most one active run. Flipping `is_active` between runs is the
-- single source of truth for what `get_orthogroup_info` returns.
CREATE UNIQUE INDEX IF NOT EXISTS orthogroup_runs_one_active_idx
  ON public.orthogroup_runs (is_active)
  WHERE is_active = true;

COMMENT ON TABLE public.orthogroup_runs IS
  'One row per OrthoFinder run. Exactly one row may be marked is_active=true at any time (enforced by partial unique index). New runs land here first; the run is then populated in `orthogroups` and activated atomically.';

-- ─── orthogroups: per-protein orthogroup mapping, scoped to a run ─────────
CREATE TABLE IF NOT EXISTS public.orthogroups (
  run_id      bigint      NOT NULL REFERENCES public.orthogroup_runs(id) ON DELETE CASCADE,
  protein_uid text        NOT NULL REFERENCES public.proteins(uid)        ON DELETE CASCADE,
  orthogroup  text        NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (run_id, protein_uid, orthogroup)
);

CREATE INDEX IF NOT EXISTS orthogroups_protein_uid_idx
  ON public.orthogroups (protein_uid);
CREATE INDEX IF NOT EXISTS orthogroups_run_orthogroup_idx
  ON public.orthogroups (run_id, orthogroup);

COMMENT ON TABLE public.orthogroups IS
  'OrthoFinder orthogroup mappings, scoped to a run. protein_uid FK enforces "no orphan genes". A protein with no mapping in the active run simply has no row — sparse by design.';

-- ─── get_orthogroup_info(query_protein_uid, result_protein_uids) ──────────
-- Reads from the active run only. If no run is active, returns zero rows.
DROP FUNCTION IF EXISTS public.get_orthogroup_info(text, text[]);

CREATE OR REPLACE FUNCTION public.get_orthogroup_info(
  query_protein_uid   text,
  result_protein_uids text[]
)
RETURNS TABLE (
  protein_uid        text,
  orthogroup         text,
  shared_with_query  boolean
)
LANGUAGE sql STABLE
AS $$
  WITH active AS (
    SELECT id FROM public.orthogroup_runs WHERE is_active = true LIMIT 1
  ),
  query_ogs AS (
    SELECT DISTINCT o.orthogroup
      FROM public.orthogroups o
     WHERE o.run_id      = (SELECT id FROM active)
       AND o.protein_uid = query_protein_uid
  )
  SELECT o.protein_uid,
         o.orthogroup,
         EXISTS (
           SELECT 1 FROM query_ogs q WHERE q.orthogroup = o.orthogroup
         ) AS shared_with_query
    FROM public.orthogroups o
   WHERE o.run_id      = (SELECT id FROM active)
     AND o.protein_uid = ANY (result_protein_uids);
$$;

COMMENT ON FUNCTION public.get_orthogroup_info(text, text[]) IS
  'For each protein_uid in result_protein_uids, returns (protein_uid, orthogroup, shared_with_query) where shared_with_query = true iff that protein shares an orthogroup with query_protein_uid in the currently-active OrthoFinder run. Returns zero rows when no run is active.';

-- ─── RLS + grants ─────────────────────────────────────────────────────────
ALTER TABLE public.orthogroup_runs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.orthogroups     ENABLE ROW LEVEL SECURITY;

-- orthogroup_runs
DROP POLICY IF EXISTS admin_all_orthogroup_runs  ON public.orthogroup_runs;
CREATE POLICY admin_all_orthogroup_runs
  ON public.orthogroup_runs FOR ALL    TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_orthogroup_runs ON public.orthogroup_runs;
CREATE POLICY agent_read_orthogroup_runs
  ON public.orthogroup_runs FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_orthogroup_runs  ON public.orthogroup_runs;
CREATE POLICY user_read_orthogroup_runs
  ON public.orthogroup_runs FOR SELECT TO bloom_user  USING (true);

-- orthogroups
DROP POLICY IF EXISTS admin_all_orthogroups  ON public.orthogroups;
CREATE POLICY admin_all_orthogroups
  ON public.orthogroups FOR ALL    TO bloom_admin USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS agent_read_orthogroups ON public.orthogroups;
CREATE POLICY agent_read_orthogroups
  ON public.orthogroups FOR SELECT TO bloom_agent USING (true);
DROP POLICY IF EXISTS user_read_orthogroups  ON public.orthogroups;
CREATE POLICY user_read_orthogroups
  ON public.orthogroups FOR SELECT TO bloom_user  USING (true);

GRANT SELECT ON public.orthogroup_runs TO bloom_user, bloom_agent;
GRANT ALL    ON public.orthogroup_runs TO bloom_admin;

GRANT SELECT ON public.orthogroups     TO bloom_user, bloom_agent;
GRANT ALL    ON public.orthogroups     TO bloom_admin;

GRANT EXECUTE ON FUNCTION public.get_orthogroup_info(text, text[])
  TO bloom_user, bloom_agent, bloom_admin;

COMMIT;
