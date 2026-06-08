-- Storage bucket + RLS for the bloommcp ↔ Supabase CSV exchange contract.
--
-- A new bucket : `bloommcp-data`. 
-- The bucket has input/output split inside expected by the bloommcp/source/supabase_client.py (`bloommcp_input/`, `bloommcp_output/`)
-- They are NOT separately RLS-scoped: 
-- any object in this bucket is covered by the same two policies  as BLOOM AGENT. 
-- bloom_agent cannot INSERT or UPDATE on any other bucket via these policies.
-- Roles and verbs:
--
--   bloom_admin  ALL     covered by the existing global `admin_all_objects`
--                        policy in 20260506000001_bloom_role_rls_policies.sql.
--
--   bloom_agent  SELECT  covered by the existing global `agent_read_objects`
--                        policy in the same file. Same reasoning.
--
--   bloom_agent  INSERT  new policy `agent_insert_bloommcp_data` below —
--                        scoped strictly to `bucket_id = 'bloommcp-data'`.
--                        Lets bloommcp upload new CSVs under either prefix.
--
--   bloom_agent  UPDATE  new policy `agent_update_bloommcp_data` below —
--                        scoped strictly to `bucket_id = 'bloommcp-data'`.
--                        Lets bloommcp overwrite an existing CSV via the
--                        Storage API's `upsert: true` branch (which routes
--                        through UPDATE, not DELETE-then-INSERT).
--
--   bloom_agent  DELETE  intentionally NOT granted. Workflow tools that
--                        re-run an analysis overwrite the previous output
--                        via upsert; outputs aren't garbage-collected by
--                        the agent. Cleanup is admin-only.
--
-- Idempotent: every CREATE POLICY is preceded by a DROP POLICY IF EXISTS;
-- the bucket INSERT uses ON CONFLICT DO NOTHING. Safe to re-run.

BEGIN;

-- ─── Bucket row ──────────────────────────────────────────────────────────────
INSERT INTO storage.buckets (id, name)
  VALUES ('bloommcp-data', 'bloommcp-data')
  ON CONFLICT DO NOTHING;

-- ─── New bucket-scoped policies ──────────────────────────────────────────────
DROP POLICY IF EXISTS agent_insert_bloommcp_data ON storage.objects;
CREATE POLICY agent_insert_bloommcp_data
  ON storage.objects
  FOR INSERT TO bloom_agent
  WITH CHECK (bucket_id = 'bloommcp-data');

DROP POLICY IF EXISTS agent_update_bloommcp_data ON storage.objects;
CREATE POLICY agent_update_bloommcp_data
  ON storage.objects
  FOR UPDATE TO bloom_agent
  USING (bucket_id = 'bloommcp-data')
  WITH CHECK (bucket_id = 'bloommcp-data');

-- Table-level GRANTs are checked BEFORE RLS — a policy that allows INSERT
-- never fires if the role lacks INSERT on storage.objects. `bloom_agent`
-- was created in 20260506000001_bloom_role_rls_policies.sql with SELECT
-- only (it was intended as a read-only role). The two policies above are
-- inert without these GRANTs. RLS still confines the writes to the
-- bloommcp-data bucket; the grant is the gate, not the scope.
GRANT INSERT, UPDATE ON storage.objects TO bloom_agent;

COMMIT;
