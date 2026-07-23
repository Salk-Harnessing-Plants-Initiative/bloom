-- Scoped DELETE grant for bloom_agent's own commit-failure cleanup (#324 gap A).
--
-- 20260605000000_create_bloommcp_data_bucket.sql deliberately withheld DELETE
-- from bloom_agent ("outputs aren't garbage-collected by the agent... cleanup
-- is admin-only"). That was correct for the steady state: a committed run's
-- objects are permanent, agent-visible history and must never be
-- agent-deletable.
--
-- #324 needs a narrower capability: when SupabaseResultStore.commit() fails
-- partway through uploading a run's outputs, it best-effort deletes only the
-- objects THAT SAME commit just uploaded. Those objects, by construction,
-- have no manifest entry yet (the manifest is written last), so nothing else
-- can be depending on them.
--
-- RLS/GRANT is a capability, not a call site: once granted, bloom_agent can
-- delete ANY object under bloommcp_output/, not only its own orphans. This
-- policy is deliberately scoped tighter than the sibling INSERT/UPDATE
-- policies (which cover the whole bucket) to bound that blast radius as much
-- as a static prefix policy can: bloom_agent can never delete anything under
-- bloommcp_input/ (source CSVs), even though INSERT/UPDATE already do.
--
-- Idempotent, matching the pattern established in 20260605000000. Reversible:
--   DROP POLICY agent_delete_bloommcp_output ON storage.objects;
--   REVOKE DELETE ON storage.objects FROM bloom_agent;

BEGIN;

DROP POLICY IF EXISTS agent_delete_bloommcp_output ON storage.objects;
CREATE POLICY agent_delete_bloommcp_output
  ON storage.objects
  FOR DELETE TO bloom_agent
  USING (bucket_id = 'bloommcp-data' AND name LIKE 'bloommcp_output/%');

-- Table-level GRANT is checked before RLS (see 20260605000000's note on this).
GRANT DELETE ON storage.objects TO bloom_agent;

COMMIT;
