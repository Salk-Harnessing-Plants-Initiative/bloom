-- Rollback for 20260901000000_add_cyl_writeback_run_scan_status.sql
-- Manual break-glass only.
--
-- WARNING (see design.md's "Rollback coupling with the bloomctl container
-- image" risk): rolling back insert_cyl_result_envelope's signature while a
-- bloomctl image built against the 2-arg signature is still live (in a GHCR
-- tag consumed by sleap-roots-pipeline's write-back WorkflowTemplate) will
-- break write-back outright — Postgres RPC dispatch is signature-based, and
-- the new image calls a signature this rollback removes. Do not run this
-- without first confirming (or accepting the consequence of not being able
-- to confirm) which bloomctl image tag is actually live. A forward fix
-- (re-applying a corrected version of the forward migration) is the safer
-- default in almost every real incident.
--
-- Drops fail_cyl_pipeline_run_scans_without_result entirely (its EXECUTE
-- grant goes with it), and restores insert_cyl_result_envelope to its prior
-- 1-arg signature and grants. Leaves cyl_pipeline_run_scans/cyl_pipeline_runs
-- and every other function untouched — this migration made no table/column
-- changes.

BEGIN;

DROP FUNCTION IF EXISTS public.fail_cyl_pipeline_run_scans_without_result(text, text);

DROP FUNCTION IF EXISTS public.insert_cyl_result_envelope(jsonb, text);

CREATE OR REPLACE FUNCTION public.insert_cyl_result_envelope(envelope jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    pinned_version constant text := '0.1.0a3';
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
BEGIN
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
    IF envelope ? 'traits' AND jsonb_typeof(envelope -> 'traits') NOT IN ('array', 'null') THEN
        RAISE EXCEPTION 'invalid envelope: traits must be an array';
    END IF;
    IF envelope ? 'blobs' AND jsonb_typeof(envelope -> 'blobs') NOT IN ('array', 'null') THEN
        RAISE EXCEPTION 'invalid envelope: blobs must be an array';
    END IF;

    IF regexp_replace(coalesce(prov ->> 'contract_version', ''), '^v', '')
       IS DISTINCT FROM regexp_replace(pinned_version, '^v', '') THEN
        RAISE EXCEPTION 'contract_version mismatch: got %, pinned % (single leading v ignored)',
            coalesce(prov ->> 'contract_version', '<null>'), pinned_version;
    END IF;

    v_idem := prov ->> 'idempotency_key';
    IF v_idem IS NULL OR length(v_idem) = 0 THEN
        RAISE EXCEPTION 'empty or absent idempotency_key';
    END IF;

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

    v_name := coalesce(prov ->> 'pipeline_run_id', 'sleap-roots:' || v_idem);
    INSERT INTO public.cyl_trait_sources (name, metadata, idempotency_key)
    VALUES (v_name, prov, v_idem)
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING id INTO v_source_id;

    IF v_source_id IS NULL THEN
        SELECT id INTO v_source_id
          FROM public.cyl_trait_sources WHERE idempotency_key = v_idem;
        RETURN jsonb_build_object(
            'source_id', v_source_id, 'scan_id', NULL,
            'trait_count', 0, 'blob_count', 0, 'was_noop', true
        );
    END IF;

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

    RETURN jsonb_build_object(
        'source_id', v_source_id, 'scan_id', v_scan_id,
        'trait_count', v_trait_count, 'blob_count', v_blob_count, 'was_noop', false
    );
END;
$fn$;

ALTER FUNCTION public.insert_cyl_result_envelope(jsonb) OWNER TO postgres;

REVOKE EXECUTE ON FUNCTION public.insert_cyl_result_envelope(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.insert_cyl_result_envelope(jsonb)
    TO bloom_writer, service_role, bloom_admin, bloom_workflows;

COMMIT;
