-- Lock down the trait-count change log's identity sequence (bloom#831).
--
-- 20260921120000 revoked every table privilege on cyl_experiment_trait_count_changes, but a new
-- sequence in `public` still picks up the default privileges: SELECT, UPDATE and USAGE for anon,
-- authenticated and service_role, and SELECT, USAGE for bloom_writer. Only the SECURITY DEFINER
-- functions write the log, and they run as the owner, so no API or app role needs the sequence.
-- The roles with no default grant are revoked too, to match the table's revoke list.
--
-- Manual rollback: supabase/rollbacks/20260921130000_revoke_cyl_experiment_trait_count_changes_sequence_rollback.sql

BEGIN;

REVOKE ALL ON SEQUENCE public.cyl_experiment_trait_count_changes_id_seq
    FROM PUBLIC, anon, authenticated, service_role,
         bloom_admin, bloom_agent, bloom_user, bloom_writer;

COMMIT;
