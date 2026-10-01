-- A no-op re-delivery finds its scan from the source row (bloom#900, bloom#875).
-- Change: fix-cyl-noop-redelivery-scan-resolution.
--
-- WHY: a no-op re-delivery (ON CONFLICT (idempotency_key) DO NOTHING) must mark the
--   re-delivering Workflow's own cyl_pipeline_run_scans row. #880's fallback found the
--   scan only through another run-scan row carrying the source, which never exists for
--   a source first written by a manual `bloomctl cyl ingest-result` or a hand-run
--   `argo submit`; those scans ended 'failed' although nothing was wrong (bloom#900,
--   reproduced on staging by Bloom run 11 over scan 12894756, source 228).
--
-- WHAT: CREATE OR REPLACE the live 2-arg insert_cyl_result_envelope(jsonb, text).
--   Everything from CREATE through the final GRANT is copied verbatim from
--   20260930120100_stamp_cyl_trait_source_recipe_and_run.sql except the no-op
--   fallback block (its comment through the END IF closing IF v_status_rows = 0):
--     1. resolve the scan from the existing source's own cyl_trait_sources.scan_id
--        (primary key), and only when that is NULL from a run-scan row carrying the
--        source, as #880 did;
--     2. the targeted UPDATE skips a row already linked to a different source.
--   Same signature and return shape, so no DROP FUNCTION. Owner, the full REVOKE
--   (20260928130100) and the GRANT are re-stated. The recipe backfill is not re-run:
--   every source created since 20260930120100 has its scan_id stamped.
--   tests/unit/test_cyl_noop_redelivery_migration_files.py enforces the diff.
--
-- Forward-only. Manual rollback (staging hot-apply only -- see its header):
--   supabase/rollbacks/20261001220000_resolve_cyl_noop_redelivery_scan_from_source_rollback.sql

BEGIN;

CREATE OR REPLACE FUNCTION public.insert_cyl_result_envelope(
    envelope jsonb,
    p_argo_workflow_name text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    pinned_version constant text := '0.1.0a9';
    prov           jsonb;
    v_idem         text;
    v_scan_key     text;
    v_req_ids      text[];
    v_n_requested  int;
    v_n_matched    int;
    v_n_scans      int;
    v_scan_id      bigint;
    v_source_id    bigint;
    v_name         text;
    v_trait        jsonb;
    v_blob         jsonb;
    v_trait_id     int;
    v_value        real;
    v_trait_count  int := 0;
    v_blob_count   int := 0;
    v_was_noop     boolean;
    v_status_rows  int;
    v_run_id       bigint;
BEGIN
    -- 1. Structural validation -------------------------------------------------
    IF envelope IS NULL OR jsonb_typeof(envelope) <> 'object' THEN
        RAISE EXCEPTION 'invalid envelope: expected a JSON object';
    END IF;
    prov := envelope -> 'provenance';
    IF prov IS NULL OR jsonb_typeof(prov) <> 'object' THEN
        RAISE EXCEPTION 'invalid envelope: missing provenance object';
    END IF;
    IF prov -> 'inputs' IS NULL OR jsonb_typeof(prov -> 'inputs') <> 'object' THEN
        RAISE EXCEPTION 'invalid envelope: missing provenance.inputs object';
    END IF;
    -- traits/blobs, when present, MUST be arrays — reject cleanly rather than let
    -- jsonb_array_elements() leak a raw "cannot extract elements from an object".
    IF envelope ? 'traits' AND jsonb_typeof(envelope -> 'traits') NOT IN ('array', 'null') THEN
        RAISE EXCEPTION 'invalid envelope: traits must be an array';
    END IF;
    IF envelope ? 'blobs' AND jsonb_typeof(envelope -> 'blobs') NOT IN ('array', 'null') THEN
        RAISE EXCEPTION 'invalid envelope: blobs must be an array';
    END IF;

    -- 2. Contract version ------------------------------------------------------
    IF regexp_replace(coalesce(prov ->> 'contract_version', ''), '^v', '')
       IS DISTINCT FROM regexp_replace(pinned_version, '^v', '') THEN
        RAISE EXCEPTION 'contract_version mismatch: got %, pinned % (single leading v ignored)',
            coalesce(prov ->> 'contract_version', '<null>'), pinned_version;
    END IF;

    -- 3. Idempotency key (opaque; never recomputed) ----------------------------
    v_idem := prov ->> 'idempotency_key';
    IF v_idem IS NULL OR length(v_idem) = 0 THEN
        RAISE EXCEPTION 'empty or absent idempotency_key';
    END IF;

    -- 4. Envelope self-consistency: one scan_key across the envelope ------------
    v_scan_key := prov ->> 'scan_key';
    IF v_scan_key IS NULL THEN
        RAISE EXCEPTION 'invalid envelope: missing provenance.scan_key';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(coalesce(envelope -> 'traits', '[]'::jsonb)) t
         WHERE t ->> 'scan_key' IS DISTINCT FROM v_scan_key
    ) THEN
        RAISE EXCEPTION 'trait scan_key disagrees with provenance.scan_key';
    END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_array_elements(coalesce(envelope -> 'blobs', '[]'::jsonb)) b
         WHERE b ->> 'scan_key' IS DISTINCT FROM v_scan_key
    ) THEN
        RAISE EXCEPTION 'blob scan_key disagrees with provenance.scan_key';
    END IF;

    -- 5. Source gate: first-writer-wins, BEFORE scan resolution.
    v_name := coalesce(prov ->> 'pipeline_run_id', 'sleap-roots:' || v_idem);
    -- add-cyl-trait-recipe-key: the Bloom run is the one run whose run-scan rows
    -- carry this Workflow name (none, or more than one, leaves it NULL). The
    -- recipe key and both run stamps are written only on a fresh insert; a no-op
    -- re-delivery leaves the existing row untouched.
    IF p_argo_workflow_name IS NOT NULL THEN
        SELECT CASE WHEN count(DISTINCT rs.run_id) = 1 THEN min(rs.run_id) END
          INTO v_run_id
          FROM public.cyl_pipeline_run_scans rs
         WHERE rs.argo_workflow_name = p_argo_workflow_name;
    END IF;
    INSERT INTO public.cyl_trait_sources
        (name, metadata, idempotency_key, recipe_key, recipe_key_version,
         argo_workflow_name, cyl_pipeline_run_id)
    VALUES (v_name, prov, v_idem, public.cyl_trait_recipe_key_v1(prov), 1,
            p_argo_workflow_name, v_run_id)
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING id INTO v_source_id;

    IF v_source_id IS NULL THEN
        SELECT id INTO v_source_id
          FROM public.cyl_trait_sources WHERE idempotency_key = v_idem;
        v_was_noop := true;
    ELSE
        v_was_noop := false;
    END IF;

    IF v_was_noop THEN
        -- Pure no-op: no scan is resolved (scan_id null) for the RETURN value,
        -- nothing further written to the trait tables. The write-back RPC's own
        -- idempotent re-delivery still needs to (re-)confirm the per-scan status
        -- if p_argo_workflow_name is supplied — e.g. a retried write-back pod
        -- calling this a second time for a scan the FIRST call already recorded.
        -- Rather than re-deriving scan_id from this delivery's own image_ids
        -- (which the "same key, different scan" short-circuit rule says must NOT
        -- govern a no-op — the run of record's own scan does), join on
        -- source_id instead: the original successful call already stamped
        -- source_id = v_source_id onto its matching cyl_pipeline_run_scans row
        -- in step 9 below, so this is the same row, found without re-resolving
        -- anything. If the original call never supplied a workflow name (or
        -- this one names a different, non-matching workflow), this affects zero
        -- rows — not an error.
        IF p_argo_workflow_name IS NOT NULL THEN
            UPDATE public.cyl_pipeline_run_scans
            SET status = 'written',
                updated_at = now()
            WHERE argo_workflow_name = p_argo_workflow_name
              AND source_id = v_source_id
              AND status != 'failed';
            GET DIAGNOSTICS v_status_rows = ROW_COUNT;

            -- bloom#875 / bloom#900 fallback: a re-delivery dispatched under a NEW
            -- workflow name always has source_id IS NULL on ITS OWN
            -- cyl_pipeline_run_scans row (source_id is set only by step 9 below and
            -- by this fallback, each together with status = 'written'), so the
            -- primary UPDATE above can never match it. Resolve the run of record's
            -- own scan from the existing source's row (cyl_trait_sources.scan_id,
            -- stamped when the source was created, or by the recipe backfill) --
            -- never from this delivery's own image_ids, preserving the "same key,
            -- different scan" rule -- and only when that is NULL from an existing
            -- run-scan row already carrying this source's id. The trait table is
            -- never read (no index on it leads on source_id), nor the intermediates
            -- table (it misses blob-less sources): the source row already holds the
            -- scan. Then retry the status update scoped to that scan within this
            -- workflow name. It skips a row linked to a different source, so a no-op
            -- never relinks another delivery's result; its "OR source_id =
            -- v_source_id" half matters only for a concurrent retry of this same
            -- source, which waits on the first retry's row lock and then re-checks
            -- the row that retry linked. With no recorded scan and no carrying row
            -- the update stays at zero rows (status_update_matched false).
            IF v_status_rows = 0 THEN
                SELECT scan_id INTO v_scan_id
                  FROM public.cyl_trait_sources
                 WHERE id = v_source_id;

                IF v_scan_id IS NULL THEN
                    SELECT scan_id INTO v_scan_id
                      FROM public.cyl_pipeline_run_scans
                     WHERE source_id = v_source_id
                     LIMIT 1;
                END IF;

                IF v_scan_id IS NOT NULL THEN
                    UPDATE public.cyl_pipeline_run_scans
                    SET status = 'written',
                        source_id = v_source_id,
                        updated_at = now()
                    WHERE argo_workflow_name = p_argo_workflow_name
                      AND scan_id = v_scan_id
                      AND status != 'failed'
                      AND (source_id IS NULL OR source_id = v_source_id);
                    GET DIAGNOSTICS v_status_rows = ROW_COUNT;
                END IF;
            END IF;
        END IF;
        RETURN jsonb_build_object(
            'source_id', v_source_id, 'scan_id', NULL,
            'trait_count', 0, 'blob_count', 0, 'was_noop', true,
            'status_update_matched',
            CASE WHEN p_argo_workflow_name IS NULL THEN NULL ELSE v_status_rows > 0 END
        );
    END IF;

    -- 6. Scan resolution via inputs.image_ids (no scan_id in the contract) ------
    SELECT array_agg(DISTINCT elem)
      INTO v_req_ids
      FROM jsonb_array_elements_text(coalesce(prov -> 'inputs' -> 'image_ids', '[]'::jsonb)) elem;

    v_n_requested := coalesce(array_length(v_req_ids, 1), 0);
    IF v_n_requested = 0 THEN
        RAISE EXCEPTION 'no image_ids: cannot resolve a scan';
    END IF;
    IF EXISTS (SELECT 1 FROM unnest(v_req_ids) r WHERE r !~ '^[0-9]+$') THEN
        RAISE EXCEPTION 'non-numeric image_id in inputs.image_ids';
    END IF;

    SELECT count(DISTINCT i.id), count(DISTINCT i.scan_id), min(i.scan_id)
      INTO v_n_matched, v_n_scans, v_scan_id
      FROM public.cyl_images i
     WHERE i.id = ANY (SELECT r::bigint FROM unnest(v_req_ids) r)
       AND i.scan_id IS NOT NULL;

    IF v_n_matched <> v_n_requested THEN
        RAISE EXCEPTION 'unresolvable image_ids: matched % of % to a scan',
            v_n_matched, v_n_requested;
    END IF;
    IF v_n_scans <> 1 THEN
        RAISE EXCEPTION 'image_ids resolve to % scans, expected exactly 1', v_n_scans;
    END IF;

    -- add-cyl-trait-recipe-key: the scan is known only now, after the source gate.
    UPDATE public.cyl_trait_sources SET scan_id = v_scan_id WHERE id = v_source_id;

    -- 7. Trait rows via the cyl_traits registry (auto-register) -----------------
    FOR v_trait IN
        SELECT * FROM jsonb_array_elements(coalesce(envelope -> 'traits', '[]'::jsonb))
    LOOP
        IF coalesce(v_trait ->> 'grain', 'scan') <> 'scan' THEN
            RAISE EXCEPTION 'non-scan-grain trait rejected (grain=%)', v_trait ->> 'grain';
        END IF;
        IF v_trait ->> 'name' IS NULL THEN
            RAISE EXCEPTION 'invalid trait: missing name';
        END IF;

        INSERT INTO public.cyl_traits (name) VALUES (v_trait ->> 'name')
        ON CONFLICT (name) DO NOTHING;
        SELECT id INTO v_trait_id FROM public.cyl_traits WHERE name = v_trait ->> 'name';

        IF jsonb_typeof(v_trait -> 'value') = 'number' THEN
            BEGIN
                v_value := (v_trait ->> 'value')::real;
            EXCEPTION WHEN numeric_value_out_of_range THEN
                v_value := NULL;
            END;
        ELSE
            v_value := NULL;
        END IF;

        INSERT INTO public.cyl_scan_traits (scan_id, source_id, trait_id, value)
        VALUES (v_scan_id, v_source_id, v_trait_id, v_value);
        v_trait_count := v_trait_count + 1;
    END LOOP;

    -- 8. Blob rows -------------------------------------------------------------
    FOR v_blob IN
        SELECT * FROM jsonb_array_elements(coalesce(envelope -> 'blobs', '[]'::jsonb))
    LOOP
        IF v_blob ->> 'file_size' IS NOT NULL AND v_blob ->> 'file_size' !~ '^[0-9]+$' THEN
            RAISE EXCEPTION 'invalid blob: file_size must be an integer, got %',
                v_blob ->> 'file_size';
        END IF;
        INSERT INTO public.cyl_scan_intermediates
            (source_id, scan_id, kind, root_type, s3_location, box_link, checksum, file_size)
        VALUES (
            v_source_id, v_scan_id,
            v_blob ->> 'kind', v_blob ->> 'root_type',
            v_blob ->> 's3_location', v_blob ->> 'box_link',
            v_blob ->> 'checksum', (v_blob ->> 'file_size')::bigint
        );
        v_blob_count := v_blob_count + 1;
    END LOOP;

    -- 9. Per-scan write-back status (bloom #696) --------------------------------
    -- Only when the caller supplied a workflow name (the write-back pod's
    -- ARGO_WORKFLOW_NAME) — manual/ad-hoc invocation with no pipeline-run
    -- context omits it and this UPDATE affects nothing. "status != 'failed'"
    -- guards against a late/out-of-order delivery resurrecting a scan the
    -- reconciliation RPC already closed out, mirroring
    -- complete_cyl_pipeline_batch's own identical guard for the identical
    -- reason. Rolls back with everything else on any earlier validation
    -- failure in this same transaction.
    --
    -- Found during /review-pr round 4: without status_update_matched below, a
    -- delivery that genuinely writes trait/blob data (was_noop=false) but whose
    -- status UPDATE is silently skipped by the guard above (the scan was already
    -- 'failed' — a real, reachable outcome of an ordinary Argo retry racing this
    -- RPC's own reconciliation call, not an exotic one) had zero caller-visible
    -- signal: the write "succeeded" and nothing said the run-level counts would
    -- now permanently disagree with the real data just written.
    IF p_argo_workflow_name IS NOT NULL THEN
        UPDATE public.cyl_pipeline_run_scans
        SET status = 'written',
            source_id = v_source_id,
            updated_at = now()
        WHERE argo_workflow_name = p_argo_workflow_name
          AND scan_id = v_scan_id
          AND status != 'failed';
        GET DIAGNOSTICS v_status_rows = ROW_COUNT;
    END IF;

    RETURN jsonb_build_object(
        'source_id', v_source_id, 'scan_id', v_scan_id,
        'trait_count', v_trait_count, 'blob_count', v_blob_count, 'was_noop', false,
        'status_update_matched',
        CASE WHEN p_argo_workflow_name IS NULL THEN NULL ELSE v_status_rows > 0 END
    );
END;
$fn$;

ALTER FUNCTION public.insert_cyl_result_envelope(jsonb, text) OWNER TO postgres;

REVOKE EXECUTE ON FUNCTION public.insert_cyl_result_envelope(jsonb, text)
    FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.insert_cyl_result_envelope(jsonb, text)
    TO bloom_writer, service_role, bloom_admin, bloom_workflows;

COMMIT;
