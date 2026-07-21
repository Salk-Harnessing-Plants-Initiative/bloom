-- Rollback for 20260625120000_create_cyl_scan_intermediates.sql
-- Manual break-glass only: this repo applies migrations forward via `supabase db push`
-- (no automated down-runner). Drops the cyl_scan_intermediates table; its policies,
-- constraints, and table-level GRANTs are removed with it. This is a greenfield leaf
-- table — no other table references it — so the DROP cannot be blocked by a dependent
-- foreign key. Any data in the table is lost.

BEGIN;

DROP TABLE IF EXISTS cyl_scan_intermediates;

COMMIT;
