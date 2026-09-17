-- Rollback for 20260917140000_fix_cyl_redelivery_status_fallback.sql
-- Manual break-glass only.
--
-- Restores insert_cyl_result_envelope's no-op branch to its pre-fallback body
-- (the primary (argo_workflow_name, source_id)-keyed UPDATE only — no
-- scan_id-scoped fallback). Same 2-arg signature throughout, so this is a
-- CREATE OR REPLACE, not a DROP FUNCTION — rollback is safe unconditionally:
-- it only removes the fallback's extra chance to match a row on a
-- cross-workflow re-delivery (bloom#875 reopens), it never removes a
-- guarantee anything else depends on. Leaves every table, every other
-- function, and both functions' grants untouched.
--
-- The function body below is copied verbatim, including its comments, from
-- 20260912110000_add_cyl_writeback_run_scan_status.sql — the exact body this
-- migration's own CREATE OR REPLACE modified.

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
    pinned_version constant text := '0.1.0a7';
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
    INSERT INTO public.cyl_trait_sources (name, metadata, idempotency_key)
    VALUES (v_name, prov, v_idem)
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

REVOKE EXECUTE ON FUNCTION public.insert_cyl_result_envelope(jsonb, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.insert_cyl_result_envelope(jsonb, text)
    TO bloom_writer, service_role, bloom_admin, bloom_workflows;

COMMIT;
