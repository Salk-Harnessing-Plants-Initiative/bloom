-- A2 changes D + E (co-landing): the idempotent cyl write-back RPC and the RLS
-- lockdown that makes it the sole writer of the cyl trait/blob tables.
--
-- D: insert_cyl_result_envelope(jsonb) — a SECURITY DEFINER function that ingests
--    one sleap-roots-contracts ResultEnvelope and writes it, in one transaction,
--    into cyl_trait_sources (provenance + idempotency anchor), cyl_scan_traits
--    (long-format trait values, via the cyl_traits registry) and
--    cyl_scan_intermediates (per-scan blob pointers). Idempotent: re-delivery of a
--    run already ingested is a pure no-op (the source insert is the atomic gate).
--
-- E: drop the legacy permissive `authenticated` INSERT policies on
--    cyl_trait_sources / cyl_scan_traits, and bloom_writer's INSERT/UPDATE policies
--    on all three tables, so only the RPC (via its postgres owner) and bloom_admin
--    can write. D and E co-land here so the forgeable-client-INSERT window never
--    opens (never D-without-E) and the write path is never broken (never E-without-D).
--
-- Owner is pinned to postgres: postgres has rolbypassrls=true and INSERT on all
-- three tables, and no table sets FORCE ROW LEVEL SECURITY, so the definer writes
-- cleanly after the lockdown. Hardened with a fixed search_path, schema-qualified
-- writes, and parameterized value binding (no format() on envelope data).
--
-- Manual rollback: supabase/rollbacks/20260630180000_add_cyl_writeback_rpc_rollback.sql

BEGIN;

CREATE OR REPLACE FUNCTION public.insert_cyl_result_envelope(envelope jsonb)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public, pg_temp
AS $fn$
DECLARE
    pinned_version constant text := 'v0.1.0a2';
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

    -- 2. Contract version ------------------------------------------------------
    IF (prov ->> 'contract_version') IS DISTINCT FROM pinned_version THEN
        RAISE EXCEPTION 'contract_version mismatch: got %, pinned %',
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

    -- 5. Scan resolution via inputs.image_ids (no scan_id in the contract) ------
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

    -- 6. Source gate: first-writer-wins. If we did not create the row, the run
    --    was already ingested in full (one txn) -> pure no-op short-circuit. ----
    v_name := coalesce(prov ->> 'pipeline_run_id', 'sleap-roots:' || v_idem);
    INSERT INTO public.cyl_trait_sources (name, metadata, idempotency_key)
    VALUES (v_name, prov, v_idem)
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING id INTO v_source_id;

    IF v_source_id IS NULL THEN
        SELECT id INTO v_source_id
          FROM public.cyl_trait_sources WHERE idempotency_key = v_idem;
        RETURN jsonb_build_object(
            'source_id', v_source_id, 'scan_id', v_scan_id,
            'trait_count', 0, 'blob_count', 0, 'was_noop', true
        );
    END IF;

    -- 7. Trait rows via the cyl_traits registry (auto-register) -----------------
    FOR v_trait IN
        SELECT * FROM jsonb_array_elements(coalesce(envelope -> 'traits', '[]'::jsonb))
    LOOP
        IF coalesce(v_trait ->> 'grain', 'scan') <> 'scan' THEN
            RAISE EXCEPTION 'non-scan-grain trait rejected (grain=%)', v_trait ->> 'grain';
        END IF;

        INSERT INTO public.cyl_traits (name) VALUES (v_trait ->> 'name')
        ON CONFLICT (name) DO NOTHING;
        SELECT id INTO v_trait_id FROM public.cyl_traits WHERE name = v_trait ->> 'name';

        -- Finite-or-null: cast-then-check. NaN/Infinity (numeric or string) and a
        -- finite value out of real range (overflow raises) all map to NULL.
        BEGIN
            v_value := (v_trait ->> 'value')::real;
            IF v_value IN ('NaN'::real, 'Infinity'::real, '-Infinity'::real) THEN
                v_value := NULL;
            END IF;
        EXCEPTION WHEN numeric_value_out_of_range OR invalid_text_representation THEN
            v_value := NULL;
        END;

        INSERT INTO public.cyl_scan_traits (scan_id, source_id, trait_id, value)
        VALUES (v_scan_id, v_source_id, v_trait_id, v_value)
        ON CONFLICT (scan_id, source_id, trait_id) DO NOTHING;
        v_trait_count := v_trait_count + 1;
    END LOOP;

    -- 8. Blob rows -------------------------------------------------------------
    FOR v_blob IN
        SELECT * FROM jsonb_array_elements(coalesce(envelope -> 'blobs', '[]'::jsonb))
    LOOP
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

-- Deterministic owner: postgres (rolbypassrls=true, INSERT on all three tables).
ALTER FUNCTION public.insert_cyl_result_envelope(jsonb) OWNER TO postgres;

-- The RPC is the sanctioned entry point: deny PUBLIC, grant the ingest roles.
REVOKE EXECUTE ON FUNCTION public.insert_cyl_result_envelope(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.insert_cyl_result_envelope(jsonb)
    TO bloom_writer, service_role, bloom_admin;

-- ---------------------------------------------------------------------------
-- Change E: lock the three trait/blob tables to RPC-only writes.
-- Drop the legacy permissive `authenticated` INSERT policies and bloom_writer's
-- direct INSERT/UPDATE policies. SELECT/admin policies are left intact, so reads
-- are unaffected and bloom_admin keeps break-glass write access. RLS (the absence
-- of a write policy), not the standing table GRANT, is the write gate.
-- ---------------------------------------------------------------------------
DROP POLICY IF EXISTS "Authenticated users can insert cyl_trait_sources" ON public.cyl_trait_sources;
DROP POLICY IF EXISTS "Authenticated users can insert cyl_scan_traits"   ON public.cyl_scan_traits;

DROP POLICY IF EXISTS writer_insert_cyl_trait_sources      ON public.cyl_trait_sources;
DROP POLICY IF EXISTS writer_update_cyl_trait_sources      ON public.cyl_trait_sources;
DROP POLICY IF EXISTS writer_insert_cyl_scan_traits        ON public.cyl_scan_traits;
DROP POLICY IF EXISTS writer_update_cyl_scan_traits        ON public.cyl_scan_traits;
DROP POLICY IF EXISTS writer_insert_cyl_scan_intermediates ON public.cyl_scan_intermediates;
DROP POLICY IF EXISTS writer_update_cyl_scan_intermediates ON public.cyl_scan_intermediates;

COMMIT;
