-- Re-pin the cyl write-back RPC's accepted contract_version to 0.1.0a7, matched
-- prefix-tolerantly (unchanged match structure). Change: repin-cyl-contract-a7 (bloom #685).
--
-- WHY: sleap-roots-contracts bumped 0.1.0a3 -> 0.1.0a7 (talmolab/sleap-roots PR #263,
--   merged 2026-08-17, adding the RunManifest type -- a producer<->producer contract never
--   emitted to JSON Schema, irrelevant to this RPC). Verified by diffing the published
--   result_envelope.schema.json at every intermediate tag (a4/a5/a6/a7 vs a3): byte-identical
--   except the $id version stamp -- a pure no-op restamp, unlike the real a2->a3 revision
--   (which added two optional Provenance fields). No Provenance/BlobRef field or type change
--   accompanies this re-pin, so no other part of the function body changes.
--
-- WHAT: CREATE OR REPLACE the function with the version block re-pinned to '0.1.0a7'; the
--   existing prefix-tolerant match (regexp_replace(...,'^v','') on both sides, compared on the
--   coalesced values) is unchanged in structure -- only the pinned_version literal moves.
--   Everything else in the function body is byte-identical to 20260706170000. Forward-only:
--   that migration (and 20260630180000 before it) is deployed + archived and MUST NOT be edited.
--
-- bloom_workflows' EXECUTE grant (added later by
-- 20260720000000_grant_bloom_workflows_writeback_rpc.sql) is preserved by this migration: GRANT
-- is additive, and the REVOKE/GRANT block below is byte-identical to the a3 migration's own
-- (bloom_writer, service_role, bloom_admin only) -- it does not REVOKE bloom_workflows, so that
-- role's EXECUTE privilege survives untouched. Do not add an explicit
-- REVOKE ... FROM bloom_workflows here or in any future re-pin of this function.
--
-- Manual rollback: supabase/rollbacks/20260831130000_cyl_writeback_contract_a7_rollback.sql

BEGIN;

-- Cutover safety gate. This migration makes the RPC permanently reject 0.1.0a3, so any
-- already-persisted a3-provenance row would silently become non-current (not corrupted --
-- metadata is opaque). No real a3 row is expected to exist yet: as of this migration, the
-- deployed trait-extractor image still stamps 0.1.0a3 and the pipeline has not been
-- redeployed against the a7-bumped contract pin (talmolab/sleap-roots-pipeline#52 tracks that
-- bump, explicitly sequenced after this migration). Single-literal check, mirroring the a3
-- migration's own single-literal a2-guard exactly (only a3-stamped rows can exist under the
-- current RPC history, since the a3 migration never accepted a2). Runs in the same
-- transaction, so a trip aborts the whole migration.
DO $guard$
DECLARE
    n_a3 bigint;
BEGIN
    SELECT count(*) INTO n_a3
      FROM public.cyl_trait_sources
     WHERE metadata ->> 'contract_version' LIKE '%0.1.0a3%';
    IF n_a3 > 0 THEN
        RAISE EXCEPTION 'a7 cutover blocked: % cyl_trait_sources row(s) carry a 0.1.0a3 contract_version; the a7 RPC would render them non-current -- resolve before applying', n_a3;
    END IF;
END
$guard$;

CREATE OR REPLACE FUNCTION public.insert_cyl_result_envelope(envelope jsonb)
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
    -- Prefix-tolerant: strip a single lowercase leading `v` from BOTH sides so the
    -- emitter's bare PEP 440 package version (0.1.0a7) and the v-prefixed git-tag/$id
    -- form (v0.1.0a7) both match. coalesce(...,'') collapses an absent/NULL value to ''
    -- BEFORE the compare, so absent and empty reject rather than slipping past a NULL.
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

    -- 5. Source gate: first-writer-wins, BEFORE scan resolution. If we did not
    --    create the row, the run was already ingested in full (one txn) -> pure
    --    no-op short-circuit. Resolving the scan first would (a) report this
    --    delivery's scan instead of the run-of-record's, and (b) turn a re-delivery
    --    whose images were since deleted into a hard error. Concurrency: under
    --    READ COMMITTED the loser of a same-key race blocks on the conflicting
    --    tuple, then DO NOTHING yields no row and the re-select sees the committed
    --    row (no NULL window). source_id is always non-null past this point.
    v_name := coalesce(prov ->> 'pipeline_run_id', 'sleap-roots:' || v_idem);
    INSERT INTO public.cyl_trait_sources (name, metadata, idempotency_key)
    VALUES (v_name, prov, v_idem)
    ON CONFLICT (idempotency_key) DO NOTHING
    RETURNING id INTO v_source_id;

    IF v_source_id IS NULL THEN
        SELECT id INTO v_source_id
          FROM public.cyl_trait_sources WHERE idempotency_key = v_idem;
        -- Pure no-op: no scan is resolved (scan_id null), nothing further written.
        RETURN jsonb_build_object(
            'source_id', v_source_id, 'scan_id', NULL,
            'trait_count', 0, 'blob_count', 0, 'was_noop', true
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

    -- min(scan_id) is the single resolved scan: safe because v_n_scans <> 1 below
    -- rejects anything but exactly one distinct non-null scan_id.
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
    --    No ON CONFLICT on the scan_traits insert: the source gate already makes
    --    re-delivery a no-op, so the only way the UNIQUE(scan_id,source_id,trait_id)
    --    could fire here is a duplicate trait name WITHIN one envelope — a malformed
    --    envelope, which is rejected (symmetric with blobs). Counts therefore equal
    --    rows written.
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

        -- Finite-or-null: only a JSON number is a value candidate; JSON null/string/
        -- bool/etc. -> NULL. A finite number beyond real range overflows on cast
        -- (raises) -> NULL. (float64 narrows to real/~7 sig-digits; magnitudes beyond
        -- float4 range are non-physical for these traits, so NULL is acceptable.)
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
    --    Like traits, no ON CONFLICT: a duplicate (kind, root_type) within one
    --    envelope is a malformed envelope and is rejected by the UNIQUE 4-tuple.
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

-- Deterministic owner: postgres (rolbypassrls=true, INSERT on all three tables).
ALTER FUNCTION public.insert_cyl_result_envelope(jsonb) OWNER TO postgres;

-- The RPC is the sanctioned entry point: deny PUBLIC, grant the ingest roles. Byte-identical
-- to the a3 migration's own block; bloom_workflows' grant (added separately, see header) is
-- unaffected by this REVOKE/GRANT pair since GRANT is additive and PUBLIC != bloom_workflows.
REVOKE EXECUTE ON FUNCTION public.insert_cyl_result_envelope(jsonb) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.insert_cyl_result_envelope(jsonb)
    TO bloom_writer, service_role, bloom_admin;

COMMIT;
