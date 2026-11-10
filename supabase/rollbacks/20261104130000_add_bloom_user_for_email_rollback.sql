-- Rollback for 20261104130000_add_bloom_user_for_email.sql
-- Manual break-glass only: this repo applies migrations forward via `supabase db push`
-- (no automated down-runner). Drops bloom_user_for_email; bloommcp then can't admit anyone
-- through the Salk connector. Safe to run twice.

BEGIN;

DROP FUNCTION IF EXISTS public.bloom_user_for_email(TEXT);

NOTIFY pgrst, 'reload schema';

COMMIT;
