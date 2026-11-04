-- Rollback for 20261104120000_add_species_notifications.sql
-- Manual break-glass only: this repo applies migrations forward via `supabase db push`
-- (no automated down-runner). Drops notification_recipients, with its rows, and
-- species.team_notified_at, with the workflows service's grant and policy on it; who gets
-- notifications and which species were emailed about are lost. Safe to run twice.

BEGIN;

DROP TABLE IF EXISTS public.notification_recipients;

DROP POLICY IF EXISTS workflows_mark_species_notified ON public.species;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'species' AND column_name = 'team_notified_at'
    ) THEN
        REVOKE UPDATE (team_notified_at) ON public.species FROM bloom_workflows;
        ALTER TABLE public.species DROP COLUMN team_notified_at;
    END IF;
END$$;

NOTIFY pgrst, 'reload schema';

COMMIT;
