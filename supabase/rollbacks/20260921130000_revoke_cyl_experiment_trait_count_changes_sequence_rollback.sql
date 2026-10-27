-- Manual rollback for 20260921130000_revoke_cyl_experiment_trait_count_changes_sequence.sql
--
-- Restores exactly what the sequence's ACL held before the revoke: `rwU` for anon, authenticated
-- and service_role, `rU` for bloom_writer. bloom_admin, bloom_agent, bloom_user and PUBLIC held no
-- direct grant on it -- the schema-wide GRANTs that cover those roles ran before this sequence
-- existed -- so there is nothing to give back to them.
--
-- ROLLBACK ORDER: roll this back before 20260921120000's rollback, which drops the table and with
-- it the sequence.

BEGIN;

GRANT ALL           ON SEQUENCE public.cyl_experiment_trait_count_changes_id_seq
    TO anon, authenticated, service_role;
GRANT USAGE, SELECT ON SEQUENCE public.cyl_experiment_trait_count_changes_id_seq TO bloom_writer;

COMMIT;
