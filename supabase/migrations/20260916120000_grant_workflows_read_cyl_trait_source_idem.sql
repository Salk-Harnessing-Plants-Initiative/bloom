-- Let bloom_workflows read cyl_trait_sources.idempotency_key.
--
-- WHY: bloomctl's write-back uploads an envelope's .slp blobs before calling
-- insert_cyl_result_envelope. The upload refuses to overwrite an object whose checksum differs
-- (correctly), while the RPC's ON CONFLICT (idempotency_key) DO NOTHING gate would have made a
-- re-delivery a benign no-op. The strict step runs first, so a re-delivery whose producer
-- recomputed its artifacts fails hard: predict's .slp output is not byte-reproducible, so the
-- same idempotency_key yields different bytes at the same (key-derived) object path.
-- See talmolab/sleap-roots-pipeline#76. source_already_ingested() checks this column before
-- uploading, which needs SELECT on it: Postgres requires column privileges for every column a
-- query REFERENCES, including in WHERE, not just those in the select-list.
--
-- THIS DOES NOT WIDEN THE LEAST-PRIVILEGE POSTURE that 20260720000000 documents
-- ("bloom_workflows stays least-privilege: read for stage-in, execute-only for write-back").
-- 20260730120000_create_cyl_pipeline_runs.sql already grants SELECT (id, metadata) on this
-- table and adds the workflows_read_cyl_trait_sources RLS policy (:167-169, :172). The RPC
-- stores metadata = prov, and v_idem := prov ->> 'idempotency_key', so the value is ALREADY
-- readable by this role through metadata->>'idempotency_key'. This grant adds an indexed access
-- path -- via the existing cyl_trait_sources_idempotency_key_key UNIQUE constraint
-- (20260609000000:22) -- to a value the role can already read, not new information.
--
-- Column-scoped deliberately. A bare `GRANT SELECT ON public.cyl_trait_sources` would reach
-- every column, including ones this role has no business reading.
--
-- Additive only: no INSERT/UPDATE/DELETE is granted here, and the execute-only posture on
-- insert_cyl_result_envelope is unchanged. Column grants accumulate in pg_attribute.attacl, so
-- the pre-existing SELECT (id, metadata) is untouched.
--
-- Not a schema grant, so tests/unit/test_schema_usage_grants.py (which matches
-- GRANT|REVOKE ... ON SCHEMA (auth|storage)) does not apply, and supabase/grants/schema_grants.sql
-- is not the right home. Precedent for a table-column grant living in a migration:
-- 20260730120000_create_cyl_pipeline_runs.sql:172.
--
-- Forward-only. Rollback lives in supabase/rollbacks/ alongside this file.

BEGIN;

GRANT SELECT (idempotency_key) ON public.cyl_trait_sources TO bloom_workflows;

COMMIT;

-- Nudge PostgREST to rebuild its schema cache. Strictly speaking a privilege change needs no
-- reload -- column ACLs are enforced by Postgres at execution and the column has existed since
-- 20260609000000 -- but deploy.yml restarts caddy and kong by name and never the `rest`
-- container, and this repo defines no pgrst_ddl_watch event trigger, so the reload would
-- otherwise rest entirely on the base image's own watcher. One line removes the uncertainty.
-- Precedent: 20240904033106_create_fix_create_cyl_dataset_function_again.sql:52.
NOTIFY pgrst, 'reload schema';
