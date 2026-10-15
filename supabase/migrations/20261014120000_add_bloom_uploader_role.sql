-- 20261014120000_add_bloom_uploader_role.sql
--
-- bloom_uploader: the role a scientist signs in as to upload their own genome versions and
-- single-cell datasets with bloomctl, without a writer's rights over the rest of Bloom.
-- An account gets it at sign-in when its app metadata has is_uploader and no workflows,
-- admin or writer flag (priority: workflows > admin > writer > uploader > user).
--
-- It reads what bloom_user reads. It writes only:
-- - genome versions it started, through start/finish/abandon_genome_version, and their two
--   files in the genome-references bucket while the version is uploading;
-- - single-cell datasets it created and has not finished: the dataset row (load columns
--   only), its clusters, genotypes, cells, genes, counts, default DE and cluster statistics,
--   and objects under h5ad/ and counts/ in the scrna bucket. It may also reopen its own
--   finished dataset that has no counts, to add them.
-- It deletes nothing and is not a member of authenticated, so the old "Authenticated users
-- can ..." policies never reach it. Storage schema USAGE is in supabase/grants/schema_grants.sql.
-- Forward-only; rollback in supabase/rollbacks/.

BEGIN;

SET LOCAL lock_timeout = '5s';

-- 1. Role ----------------------------------------------------------------------------------

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'bloom_uploader') THEN
    CREATE ROLE bloom_uploader NOLOGIN;
  END IF;
END
$$;

GRANT bloom_uploader TO authenticator;
GRANT USAGE ON SCHEMA public TO bloom_uploader;

-- 2. Sign-in -------------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.custom_access_token_hook(event jsonb)
 RETURNS jsonb
 LANGUAGE plpgsql
AS $function$
DECLARE
  claims       JSONB;
  is_admin     BOOLEAN;
  is_writer    BOOLEAN;
  is_workflows BOOLEAN;
  is_uploader  BOOLEAN;
BEGIN
  claims := event->'claims';

  SELECT COALESCE(
    (SELECT (raw_app_meta_data->>'is_admin')::boolean
     FROM auth.users WHERE id = (claims->>'sub')::uuid),
    false
  ) INTO is_admin;

  SELECT COALESCE(
    (SELECT (raw_app_meta_data->>'is_writer')::boolean
     FROM auth.users WHERE id = (claims->>'sub')::uuid),
    false
  ) INTO is_writer;

  SELECT COALESCE(
    (SELECT (raw_app_meta_data->>'is_workflows')::boolean
     FROM auth.users WHERE id = (claims->>'sub')::uuid),
    false
  ) INTO is_workflows;

  SELECT COALESCE(
    (SELECT (raw_app_meta_data->>'is_uploader')::boolean
     FROM auth.users WHERE id = (claims->>'sub')::uuid),
    false
  ) INTO is_uploader;

  -- bloom_agent is set out-of-band via static JWT in BLOOM_AGENT_KEY, not here.
  IF is_workflows THEN
    claims := jsonb_set(claims, '{role}', '"bloom_workflows"');
  ELSIF is_admin THEN
    claims := jsonb_set(claims, '{role}', '"bloom_admin"');
  ELSIF is_writer THEN
    claims := jsonb_set(claims, '{role}', '"bloom_writer"');
  ELSIF is_uploader THEN
    claims := jsonb_set(claims, '{role}', '"bloom_uploader"');
  ELSE
    claims := jsonb_set(claims, '{role}', '"bloom_user"');
  END IF;

  event := jsonb_set(event, '{claims}', claims);
  RETURN event;
END;
$function$;

-- 3. Reads: what bloom_user reads ----------------------------------------------------------

-- Copies bloom_user's table reads, read rules and function grants as they stand, except the
-- functions that write.
DO $$
DECLARE
  r RECORD;
  v_name TEXT;
BEGIN
  FOR r IN
    SELECT n.nspname, c.relname
      FROM pg_class c
      JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname IN ('public', 'storage')
       AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
       AND has_table_privilege('bloom_user', c.oid, 'SELECT')
  LOOP
    EXECUTE format('GRANT SELECT ON %I.%I TO bloom_uploader', r.nspname, r.relname);
  END LOOP;

  FOR r IN
    SELECT p.schemaname, p.tablename, p.policyname, p.qual
      FROM pg_policies p
     WHERE 'bloom_user' = ANY (p.roles)
       AND p.cmd = 'SELECT'
       AND p.permissive = 'PERMISSIVE'
       AND p.schemaname IN ('public', 'storage')
  LOOP
    v_name := left('uploader_' || regexp_replace(r.policyname, '^user_', ''), 63);
    EXECUTE format('DROP POLICY IF EXISTS %I ON %I.%I', v_name, r.schemaname, r.tablename);
    EXECUTE format('CREATE POLICY %I ON %I.%I FOR SELECT TO bloom_uploader USING (%s)',
                   v_name, r.schemaname, r.tablename, coalesce(r.qual, 'true'));
  END LOOP;

  FOR r IN
    SELECT DISTINCT p.oid::regprocedure AS fn
      FROM pg_proc p, aclexplode(p.proacl) a
     WHERE p.pronamespace = 'public'::regnamespace
       AND a.grantee = 'bloom_user'::regrole
       AND a.privilege_type = 'EXECUTE'
       AND p.proname NOT IN ('add_species', 'insert_gravi_image', 'insert_gravi_scan_session',
                             'insert_gravi_scan_metadata')
  LOOP
    EXECUTE format('GRANT EXECUTE ON FUNCTION %s TO bloom_uploader', r.fn);
  END LOOP;
END
$$;

-- 4. Ownership checks ----------------------------------------------------------------------

-- Each runs as its owner, because a policy runs as the caller and bloom_uploader has no
-- auth schema access to call auth.uid() itself.

-- True for a live dataset the caller created and has not finished.
CREATE OR REPLACE FUNCTION public.uploader_may_load_scrna_dataset(p_dataset_id BIGINT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.scrna_datasets d
     WHERE d.id = p_dataset_id
       AND d.created_by = auth.uid()
       AND d.ingested_at IS NULL
       AND d.deleted_at IS NULL
  );
$$;

-- True for a live dataset the caller created and finished without counts, which bloomctl
-- reopens to add them.
CREATE OR REPLACE FUNCTION public.uploader_may_reopen_scrna_dataset(p_dataset_id BIGINT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.scrna_datasets d
     WHERE d.id = p_dataset_id
       AND d.created_by = auth.uid()
       AND d.ingested_at IS NOT NULL
       AND d.deleted_at IS NULL
       AND NOT EXISTS (SELECT 1 FROM public.scrna_counts c WHERE c.dataset_id = d.id)
  );
$$;

-- True when the object is one of the two files of a version the caller started and is
-- still uploading.
CREATE OR REPLACE FUNCTION public.uploader_may_upload_genome_file(p_name TEXT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.genome_reference_versions v
     WHERE v.status = 'uploading'
       AND v.created_by = auth.uid()
       AND p_name IN (v.fasta_path, v.gtf_path)
  );
$$;

-- True when a storage object's owner is the caller.
CREATE OR REPLACE FUNCTION public.uploader_owns_object(p_owner_id TEXT)
RETURNS BOOLEAN
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT p_owner_id IS NOT NULL AND p_owner_id = auth.uid()::TEXT;
$$;

REVOKE EXECUTE ON FUNCTION public.uploader_may_load_scrna_dataset(BIGINT),
  public.uploader_may_reopen_scrna_dataset(BIGINT),
  public.uploader_may_upload_genome_file(TEXT),
  public.uploader_owns_object(TEXT)
  FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.uploader_may_load_scrna_dataset(BIGINT),
  public.uploader_may_reopen_scrna_dataset(BIGINT),
  public.uploader_may_upload_genome_file(TEXT),
  public.uploader_owns_object(TEXT)
  TO bloom_uploader;

-- 5. Genome versions -----------------------------------------------------------------------

-- finish and abandon already refuse a version someone else started.
GRANT EXECUTE ON FUNCTION public.start_genome_version(TEXT, BIGINT, TEXT, TEXT, TEXT, TEXT, TEXT)
  TO bloom_uploader;
GRANT EXECUTE ON FUNCTION public.finish_genome_version(BIGINT, TEXT, BIGINT, TEXT, BIGINT)
  TO bloom_uploader;
GRANT EXECUTE ON FUNCTION public.abandon_genome_version(BIGINT) TO bloom_uploader;

-- 6. Single-cell datasets ------------------------------------------------------------------

-- Only the columns the load writes, so the uploader cannot set a dataset's owner or finish
-- time when creating it, nor later change its name, species or owner.
GRANT INSERT (name, species_id, source_checksum, metadata) ON public.scrna_datasets
  TO bloom_uploader;
GRANT UPDATE (n_cells, n_genes, expression_units, metadata, ingested_at) ON public.scrna_datasets
  TO bloom_uploader;

GRANT INSERT ON public.scrna_clusters, public.scrna_genotypes, public.scrna_cells,
  public.scrna_genes, public.scrna_counts, public.scrna_de_runs, public.scrna_de,
  public.scrna_de_genes, public.scrna_cluster_stats TO bloom_uploader;

-- scrna_cells' CHECK calls it with the inserting role's privileges.
GRANT EXECUTE ON FUNCTION public.scrna_facets_are_flat_text(JSONB) TO bloom_uploader;

DROP POLICY IF EXISTS uploader_insert_scrna_datasets ON public.scrna_datasets;
CREATE POLICY uploader_insert_scrna_datasets ON public.scrna_datasets
  FOR INSERT TO bloom_uploader
  WITH CHECK (true);

-- Finishing sets ingested_at, so the new row is not checked against the unfinished rule; the
-- column grants above already limit what changes.
DROP POLICY IF EXISTS uploader_update_scrna_datasets ON public.scrna_datasets;
CREATE POLICY uploader_update_scrna_datasets ON public.scrna_datasets
  FOR UPDATE TO bloom_uploader
  USING (public.uploader_may_load_scrna_dataset(id)
         OR public.uploader_may_reopen_scrna_dataset(id))
  WITH CHECK (true);

DO $$
DECLARE
  t TEXT;
BEGIN
  FOREACH t IN ARRAY ARRAY['scrna_clusters', 'scrna_genotypes', 'scrna_cells', 'scrna_genes',
                           'scrna_counts', 'scrna_de_runs', 'scrna_de', 'scrna_de_genes',
                           'scrna_cluster_stats']
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON public.%I', 'uploader_insert_' || t, t);
    EXECUTE format(
      'CREATE POLICY %I ON public.%I FOR INSERT TO bloom_uploader '
      'WITH CHECK (public.uploader_may_load_scrna_dataset(dataset_id))',
      'uploader_insert_' || t, t);
  END LOOP;
END
$$;

-- 7. Storage -------------------------------------------------------------------------------

GRANT SELECT, INSERT, UPDATE ON storage.objects TO bloom_uploader;

-- The load stores the file under h5ad/ and each gene's counts under counts/, replacing a
-- counts file its own stopped load left.
DROP POLICY IF EXISTS uploader_insert_scrna ON storage.objects;
CREATE POLICY uploader_insert_scrna ON storage.objects
    FOR INSERT TO bloom_uploader
    WITH CHECK (bucket_id = 'scrna' AND (name LIKE 'h5ad/%' OR name LIKE 'counts/%'));

DROP POLICY IF EXISTS uploader_update_scrna ON storage.objects;
CREATE POLICY uploader_update_scrna ON storage.objects
    FOR UPDATE TO bloom_uploader
    USING (bucket_id = 'scrna' AND (name LIKE 'h5ad/%' OR name LIKE 'counts/%')
           AND public.uploader_owns_object(owner_id))
    WITH CHECK (bucket_id = 'scrna' AND (name LIKE 'h5ad/%' OR name LIKE 'counts/%')
                AND public.uploader_owns_object(owner_id));

-- A genome version's two files, added once; never replaced.
DROP POLICY IF EXISTS uploader_insert_own_genome_upload ON storage.objects;
CREATE POLICY uploader_insert_own_genome_upload ON storage.objects
    FOR INSERT TO bloom_uploader
    WITH CHECK (bucket_id = 'genome-references'
                AND public.uploader_may_upload_genome_file(name));

-- Bounds every write to these two buckets, whatever other policy applies to all roles.
DROP POLICY IF EXISTS uploader_insert_only_upload_buckets ON storage.objects;
CREATE POLICY uploader_insert_only_upload_buckets ON storage.objects
    AS RESTRICTIVE
    FOR INSERT TO bloom_uploader
    WITH CHECK (bucket_id IN ('scrna', 'genome-references'));

DROP POLICY IF EXISTS uploader_update_only_scrna ON storage.objects;
CREATE POLICY uploader_update_only_scrna ON storage.objects
    AS RESTRICTIVE
    FOR UPDATE TO bloom_uploader
    USING (bucket_id = 'scrna')
    WITH CHECK (bucket_id = 'scrna');

-- PostgREST serves RPCs from a cached schema.
NOTIFY pgrst, 'reload schema';

COMMIT;
