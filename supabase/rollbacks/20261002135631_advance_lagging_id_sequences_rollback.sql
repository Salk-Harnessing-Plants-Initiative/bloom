-- Rollback for: 20261002135631_advance_lagging_id_sequences.sql (bloom#1022)
--
-- STAGING HOT-APPLY ONLY. Never run this on prod as a "fix": a rollback in prod means a
-- new forward migration instead.
--
-- This rollback deliberately does nothing to any sequence. The migration only moved
-- sequences that were behind their data forward to max(id). The values they had before
-- were exactly the bug: putting a sequence back behind its data would make the next
-- default-id insert collide with an existing row again. The migration changed no schema,
-- so there is nothing else to undo.
--
-- To let `supabase db push` apply the migration again (it is safe to re-apply; a
-- re-run advances nothing that isn't behind), mark it reverted:
--   supabase migration repair --status reverted 20261002135631

SELECT 'advance_lagging_id_sequences rollback: nothing to undo' AS note;
