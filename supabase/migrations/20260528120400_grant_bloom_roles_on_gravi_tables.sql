-- Grant the bloom roles the table-level privileges they need on the gravi
-- tables (and metadata sub-tables).
--
-- Background: 20260414002000_security_groups.sql grants bloom_user / bloom_admin
-- / bloom_agent on every table that existed when it ran. The gravi tables were
-- added later (port from V1, commit 3652654) and therefore didn't pick up those
-- catch-all grants. RLS policies for bloom_user exist on these tables but the
-- table-level GRANT is the gate before RLS applies — without it, queries hit
-- "permission denied for table gravi_*".

GRANT SELECT ON
  public.gravi_scanners,
  public.gravi_experiments,
  public.gravi_scans,
  public.gravi_scan_sessions,
  public.gravi_images,
  public.gravi_scan_metadata_sections,
  public.gravi_scan_metadata_section_plants,
  public.gravi_scan_metadata_accession
TO bloom_user, bloom_agent;

GRANT SELECT, INSERT, UPDATE, DELETE ON
  public.gravi_scanners,
  public.gravi_experiments,
  public.gravi_scans,
  public.gravi_scan_sessions,
  public.gravi_images,
  public.gravi_scan_metadata_sections,
  public.gravi_scan_metadata_section_plants,
  public.gravi_scan_metadata_accession
TO bloom_admin;
