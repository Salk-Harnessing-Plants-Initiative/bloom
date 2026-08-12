-- Convenience view for reading a plate (GraviScan) capture with everything about it in one
-- place, backing `bloomctl plate download`. The gravi-side analog of cyl_scans_extended.
--
-- Two deliberate differences from cyl_scans_extended:
--
-- 1. security_invoker = true. A plain view runs with its owner's rights and would bypass the
--    RLS policies on the gravi tables. cyl_scans_extended predates the bloom_role pattern;
--    everything added since (recent_phenotypes_by_plate_scanner) sets this, and so does this.
--
-- 2. Every join except gravi_experiments is a LEFT join. cyl_scans_extended can use inner
--    joins because its chain is NOT NULL end to end. This one cannot: species_id, scanner_id,
--    session_id and metadata_id are all nullable, and an inner join would silently drop scans
--    that have images. A download that quietly omits rows is worse than one that fails.
--
-- accession_id/accession_name come from the per-plate metadata rather than
-- gravi_experiments.accession_id, because the desktop writes the metadata row per scan.

DROP VIEW IF EXISTS gravi_scans_extended;

CREATE VIEW gravi_scans_extended
WITH (security_invoker = true) AS
    SELECT
        s.id                AS scan_id,
        s.plate_id          AS plate_id,
        s.capture_date      AS capture_date,
        s.uploaded_at       AS uploaded_at,
        s.cycle_number      AS cycle_number,
        s.grid_mode         AS grid_mode,
        s.plate_index       AS plate_index,
        s.resolution        AS resolution,
        s.format            AS format,
        s.wave_number       AS wave_number,
        s.transplant_date   AS transplant_date,
        s.custom_note       AS custom_note,
        s.scanner_id        AS scanner_id,
        sc.name             AS scanner_name,
        s.phenotyper_id     AS phenotyper_id,
        s.session_id        AS session_id,
        ses.scan_mode       AS scan_mode,
        e.id                AS experiment_id,
        e.name              AS experiment_name,
        e.system_name       AS system_name,
        sp.id               AS species_id,
        sp.common_name      AS species_name,
        sp.genus            AS species_genus,
        sp.species          AS species_species,
        s.metadata_id       AS metadata_id,
        m.accession_id      AS accession_id,
        m.accession_name    AS accession_name
    FROM gravi_scans s
    JOIN      gravi_experiments             e   ON e.id   = s.experiment_id
    LEFT JOIN species                       sp  ON sp.id  = e.species_id
    LEFT JOIN gravi_scanners                sc  ON sc.id  = s.scanner_id
    LEFT JOIN gravi_scan_sessions           ses ON ses.id = s.session_id
    LEFT JOIN gravi_scan_metadata_accession m   ON m.id   = s.metadata_id;

-- Supabase's default privileges grant SELECT on every new object in `public` to anon and
-- authenticated. Neither should reach plate scans: the JWT hook
-- (20260519140000_jwt_hook_read_app_meta_data.sql) stamps every signed-in user as
-- bloom_user / bloom_writer / bloom_admin, so no real caller is ever plain `anon`.
-- security_invoker + the absence of an anon RLS policy already yields zero rows, but the
-- grant itself is an accident of the platform rather than an intent — same reason
-- cyl_experiment_search revokes it.
REVOKE ALL ON public.gravi_scans_extended FROM PUBLIC;
REVOKE ALL ON public.gravi_scans_extended FROM anon;

-- The gravi tables were added after 20260414002000_security_groups.sql's catch-all grants, so
-- new objects over them need their grants stated explicitly (same reason as
-- 20260528120400_grant_bloom_roles_on_gravi_tables.sql). Read-only: the view is a read
-- surface. bloom_writer reaches it by inheriting bloom_user.
GRANT SELECT ON public.gravi_scans_extended TO bloom_user, bloom_agent, bloom_admin;
