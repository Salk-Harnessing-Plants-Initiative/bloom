-- CI-only helper: seed a synthetic cylinder-style experiment with real trait
-- data for the dev-stack-smoke job's granular smoke tests (bloom#551 review
-- round 2 -- SupabaseReader's raw tier is DB-only, so those smoke tools need a
-- real numeric experiment id with trait rows, not a filename).
--
-- NOT a migration: defines a throwaway function against the CI job's own
-- disposable Postgres (dropped with the whole container at job teardown),
-- never applied to dev/staging/prod. Run with:
--   docker compose -f docker-compose.dev.yml --env-file .env.dev exec -T db-dev \
--     psql -U supabase_admin -d postgres -v ON_ERROR_STOP=1 \
--     < scripts/ci/seed_smoke_experiments.sql
-- then call public._seed_smoke_experiment(...) separately per experiment (see
-- the "Seed DB experiments" step in .github/workflows/pr-checks.yml) to
-- capture each returned experiment id via `psql -tAc`.
--
-- Seeds by direct INSERT into cyl_scan_traits with source_id left NULL --
-- mirroring the "legacy NULL-source" seeding pattern both
-- scripts/seed_cyl_mock_data.sql and tests/integration/test_cyl_read_path.py
-- already document as sanctioned for supabase_admin (bypasses the change-E
-- RLS lockdown). Deliberately NOT using insert_cyl_result_envelope: that RPC
-- mints one new cyl_trait_sources row per call (one call per scan), so
-- looping it per-plant would give every plant its own distinct source_id --
-- SupabaseReader's raw tier always pins to ONE experiment-wide source (see
-- supabase_reader.py's module docstring, Decision D2), and an unpinned
-- resolve_source() call would then only ever see the single plant whose
-- source_id happens to be the experiment-wide max. Leaving source_id NULL
-- for every row sidesteps that entirely: cyl_scan_traits_source's
-- `is_latest` compares NULL to NULL (IS NOT DISTINCT FROM) and is trivially
-- true for every row, so every plant is "latest" together.
--
-- One scan per plant, matching SupabaseReader's pivot (MultipleScansPerPlantError
-- otherwise) -- unlike scripts/seed_cyl_mock_data.sql's 6-scans-per-plant
-- time series, which is for a different (local dev, growth-over-time) purpose.

CREATE OR REPLACE FUNCTION public._seed_smoke_experiment(
    p_name text, p_n_plants int, p_n_traits int
) RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    v_species_id    bigint;
    v_experiment_id bigint;
    v_wave_id       bigint;
    v_accession_id  bigint;
    v_plant_id      bigint;
    v_scan_id       bigint;
    v_trait_id      int;
    i               int;
    j               int;
    v_value         numeric;
BEGIN
    INSERT INTO species DEFAULT VALUES RETURNING id INTO v_species_id;
    INSERT INTO cyl_experiments (name, species_id)
        VALUES (p_name, v_species_id) RETURNING id INTO v_experiment_id;
    INSERT INTO cyl_waves (experiment_id, number)
        VALUES (v_experiment_id, 1) RETURNING id INTO v_wave_id;

    FOR j IN 1..p_n_traits LOOP
        INSERT INTO cyl_traits (name) VALUES (p_name || '_trait_' || j::text)
            ON CONFLICT (name) DO NOTHING;
    END LOOP;

    FOR i IN 1..p_n_plants LOOP
        INSERT INTO accessions (name) VALUES (p_name || '-acc-' || i::text)
            RETURNING id INTO v_accession_id;
        INSERT INTO cyl_plants (wave_id, accession_id, germ_day, qr_code)
            VALUES (v_wave_id, v_accession_id, 5, p_name || '-qr-' || i::text)
            RETURNING id INTO v_plant_id;
        INSERT INTO cyl_scans (plant_id, date_scanned, plant_age_days)
            VALUES (v_plant_id, '2026-01-01', 10) RETURNING id INTO v_scan_id;

        FOR j IN 1..p_n_traits LOOP
            SELECT id INTO v_trait_id FROM cyl_traits
                WHERE name = p_name || '_trait_' || j::text;
            -- Deterministic but non-collinear across traits: a distinct
            -- per-trait slope (i * (1 + (j%5)*0.3)) plus a per-(i,j) modular
            -- wobble, so the covariance matrix isn't degenerate by
            -- construction (real per-trait correlation still varies) --
            -- needed for PCA/clustering/mahalanobis to fit meaningfully
            -- rather than on perfectly collinear synthetic columns.
            v_value := round(
                (10 + i * (1.0 + (j % 5) * 0.3) + ((i * j) % 11) * 0.4)::numeric,
                3
            );
            INSERT INTO cyl_scan_traits (scan_id, trait_id, value)
                VALUES (v_scan_id, v_trait_id, v_value);
        END LOOP;
    END LOOP;

    RETURN v_experiment_id;
END;
$$;
