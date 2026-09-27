-- Manual rollback for 20260916120000_grant_workflows_read_cyl_trait_source_idem.sql
--
-- Revokes ONLY the idempotency_key column. SELECT (id, metadata) predates this migration
-- (20260730120000_create_cyl_pipeline_runs.sql:172) and the dedup-preview read path runs on it,
-- so this must never be widened to a bare `REVOKE SELECT ON public.cyl_trait_sources`, which
-- would strip those too. Likewise the workflows_read_cyl_trait_sources RLS policy is left in
-- place: it predates this migration.
--
-- SAFE TO APPLY WITHOUT REDEPLOYING bloomctl, but only because source_already_ingested()
-- catches every exception and returns False. After this revoke the check degrades to
-- "not already ingested" and the command falls back to its previous upload-then-RPC behaviour.
--
-- That safety is load-bearing on the BREADTH of that except clause. Narrowing it to
-- postgrest.APIError would still catch the 42501 this revoke produces, but narrowing it further
-- -- or removing the fallback as "dead code" -- converts this rollback from "degrade" into
-- "every re-delivery raises". bloomcli/tests/test_cyl_ingest.py pins the fallback against
-- non-APIError exception types for exactly this reason.

BEGIN;

REVOKE SELECT (idempotency_key) ON public.cyl_trait_sources FROM bloom_workflows;

COMMIT;

NOTIFY pgrst, 'reload schema';
