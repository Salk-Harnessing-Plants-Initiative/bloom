-- Read-only dry run of add-cyl-trait-recipe-key's backfill against a real database
-- (tasks.md 7.2 staging, 8.0 production). Run with default_transaction_read_only=on.
-- Nothing here writes; the recipe helpers and columns do not exist yet on the target.
--
-- The payload expression and the scan-resolution CTE are copied from
-- supabase/migrations/*_add_cyl_trait_recipe_key.sql. The only change is dropping
-- `s.scan_id IS NULL` (the column does not exist before the migration).
-- tests/unit/test_cyl_trait_recipe_migration_files.py pins that they stay identical.
--
-- Output: one row of counts. Expected on staging (2026-09-29): 85 sources, 85 keyed,
-- 10 distinct pipeline keys, 80 of 80 object-metadata sources resolved, 79 of 79 agreeing
-- with their trait rows. empty_payload counts object-metadata sources with no models and no
-- code shas, which all share one recipe key (design D1); it is expected to be 0. unplaced
-- counts the sources that would keep a NULL scan_id (5 on staging); every recipe read probes
-- each selected scan against each of them.

WITH keyed AS (
    SELECT src.id,
           CASE WHEN jsonb_typeof(src.metadata) = 'object'
                THEN encode(sha256(convert_to(p.payload::text, 'UTF8')), 'hex')
                ELSE 'legacy:' || src.id END AS recipe_key,
           p.payload
      FROM (SELECT id, metadata FROM public.cyl_trait_sources) AS src
      CROSS JOIN LATERAL (
-- BEGIN payload (copied from cyl_trait_recipe_payload_v1)
    SELECT CASE
        WHEN metadata IS NULL OR jsonb_typeof(metadata) <> 'object' THEN NULL
        ELSE jsonb_build_object(
                 'models', coalesce((
                     SELECT jsonb_agg(t.triple ORDER BY t.triple::text COLLATE "C")
                       FROM (
                           SELECT jsonb_build_array(
                                      m ->> 'registry_id', m ->> 'version', m ->> 'weights_checksum'
                                  ) AS triple
                             FROM jsonb_array_elements(
                                      CASE WHEN jsonb_typeof(metadata -> 'predict_models') = 'array'
                                           THEN metadata -> 'predict_models'
                                           ELSE '[]'::jsonb END
                                  ) AS m
                       ) AS t
                 ), '[]'::jsonb),
                 'predict_code_sha', metadata ->> 'predict_code_sha',
                 'traits_code_sha', metadata ->> 'traits_code_sha'
             )
             || CASE
                    WHEN jsonb_typeof(metadata -> 'predict_output_params') = 'object'
                         AND metadata -> 'predict_output_params' <> '{}'::jsonb
                    THEN jsonb_build_object('predict_output_params', metadata -> 'predict_output_params')
                    ELSE '{}'::jsonb
                END
    END
-- END payload
      ) AS p(payload)
), resolution AS (
-- BEGIN resolution (copied from cyl_backfill_trait_source_recipe_identity)
    WITH candidates AS (
        SELECT s.id, s.metadata -> 'inputs' -> 'image_ids' AS image_ids
          FROM public.cyl_trait_sources s
         WHERE jsonb_typeof(s.metadata) = 'object'
           AND jsonb_typeof(s.metadata -> 'inputs' -> 'image_ids') = 'array'
    ), elements AS (
        SELECT c.id, e.value AS image_id
          FROM candidates c, jsonb_array_elements_text(c.image_ids) AS e
    ), resolved AS (
        SELECT el.id,
               bool_and(coalesce(el.image_id ~ '^[0-9]{1,18}$', false)) AS all_numeric,
               count(DISTINCT el.image_id) AS n_requested,
               count(DISTINCT i.id) AS n_matched,
               count(DISTINCT i.scan_id) AS n_scans,
               min(i.scan_id) AS scan_id
          FROM elements el
          LEFT JOIN public.cyl_images i
                 ON i.id = CASE WHEN el.image_id ~ '^[0-9]{1,18}$' THEN el.image_id::bigint END
                AND i.scan_id IS NOT NULL
         GROUP BY el.id
    )
-- END resolution
    SELECT r.id, r.scan_id
      FROM resolved r
     WHERE r.all_numeric AND r.n_matched = r.n_requested AND r.n_scans = 1
), agreement AS (
    SELECT r.id,
           bool_and(t.scan_id = r.scan_id) AS agrees
      FROM resolution r
      JOIN public.cyl_scan_traits t ON t.source_id = r.id
     GROUP BY r.id
)
SELECT (SELECT count(*) FROM public.cyl_trait_sources) AS sources,
       (SELECT count(*) FROM keyed WHERE recipe_key IS NOT NULL) AS keyed,
       (SELECT count(DISTINCT recipe_key) FROM keyed WHERE recipe_key NOT LIKE 'legacy:%') AS pipeline_keys,
       (SELECT count(*) FROM keyed
         WHERE payload = '{"models": [], "traits_code_sha": null, "predict_code_sha": null}'::jsonb)
           AS empty_payload,
       (SELECT count(*) FROM public.cyl_trait_sources WHERE jsonb_typeof(metadata) = 'object') AS object_metadata,
       (SELECT count(*) FROM resolution) AS resolved,
       (SELECT count(*) FROM public.cyl_trait_sources)
           - (SELECT count(*) FROM resolution) AS unplaced,
       (SELECT count(*) FROM agreement) AS with_trait_rows,
       (SELECT count(*) FROM agreement WHERE agrees) AS agreeing;
