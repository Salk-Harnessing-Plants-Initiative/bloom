-- Renames the storage bucket from the underscore-named `species_illustrations`
-- to the S3-compliant kebab-case `species-illustrations`. S3 bucket names
-- only allow lowercase alphanumerics + hyphens (3–63 chars); MinIO's strict
-- mode refuses to create the underscore name, which previously caused
-- `mc mb -p local/species_illustrations` to fail silently in CI (masked by a
-- `|| true`). Frontend / SDK / docs / init script are all updated in this PR
-- to read/write the new name.
--
-- Idempotent + safely re-runnable: every CREATE POLICY is preceded by a
-- DROP POLICY IF EXISTS on the new (hyphen) name as well as the old
-- (underscore) name, so partial application, manual hyphen-policy patch,
-- or rollback-then-replay all converge to the same state without errors.
--
-- The legacy `species_illustrations` storage.buckets row is intentionally
-- left in place: dropping it here would break anything still pointing at it
-- while data migration is ongoing. A follow-up migration can drop the
-- legacy row once any existing objects have been moved.

BEGIN;

INSERT INTO storage.buckets (id, name)
  VALUES ('species-illustrations', 'species-illustrations')
    ON CONFLICT DO NOTHING;

-- Repoint any objects filed under the old underscore-bucket id onto the
-- hyphen id so they remain readable under the new policies.
--
-- storage.objects is unique on (bucket_id, name), so move only rows with no
-- counterpart under the new id. Rows that have one are left in place: matching
-- names do not prove matching content, and that can only be established per
-- environment. Nothing reads the legacy id, so leaving them is inert.
UPDATE storage.objects o
   SET bucket_id = 'species-illustrations'
 WHERE o.bucket_id = 'species_illustrations'
   AND NOT EXISTS (
     SELECT 1
       FROM storage.objects t
      WHERE t.bucket_id = 'species-illustrations'
        AND t.name = o.name
   );

-- Repoint the four authenticated-role policies from the original
-- 20230807221141_create_species_illustrations_bucket.sql migration.
DROP POLICY IF EXISTS "Authenticated users can select species_illustrations" ON storage.objects;
DROP POLICY IF EXISTS "Authenticated users can select species-illustrations" ON storage.objects;
CREATE POLICY "Authenticated users can select species-illustrations" ON storage.objects
    FOR SELECT TO authenticated
    USING (bucket_id = 'species-illustrations');

DROP POLICY IF EXISTS "Authenticated users can update species_illustrations" ON storage.objects;
DROP POLICY IF EXISTS "Authenticated users can update species-illustrations" ON storage.objects;
CREATE POLICY "Authenticated users can update species-illustrations" ON storage.objects
    FOR UPDATE TO authenticated
    USING (bucket_id = 'species-illustrations');

DROP POLICY IF EXISTS "Authenticated users can delete species_illustrations" ON storage.objects;
DROP POLICY IF EXISTS "Authenticated users can delete species-illustrations" ON storage.objects;
CREATE POLICY "Authenticated users can delete species-illustrations" ON storage.objects
    FOR DELETE TO authenticated
    USING (bucket_id = 'species-illustrations');

DROP POLICY IF EXISTS "Authenticated users can insert species_illustrations" ON storage.objects;
DROP POLICY IF EXISTS "Authenticated users can insert species-illustrations" ON storage.objects;
CREATE POLICY "Authenticated users can insert species-illustrations" ON storage.objects
    FOR INSERT TO authenticated
    WITH CHECK (bucket_id = 'species-illustrations');

-- Repoint the bloom_user read policy from
-- 20260506000001_bloom_role_rls_policies.sql.
DROP POLICY IF EXISTS user_read_species_illustrations ON storage.objects;
CREATE POLICY user_read_species_illustrations ON storage.objects
    FOR SELECT TO bloom_user
    USING (bucket_id = 'species-illustrations');

COMMIT;
