-- Read-model views over cylinder accessions, backing `bloomctl cyl accessions`.
--
-- Chain: cyl_plants (accession_id, wave_id) -> cyl_waves (experiment_id)
--        -> cyl_experiments (species_id) -> species, plus accessions for the name.
-- Built on the base tables so security_invoker applies cleanly to the underlying RLS.
-- Both do their DISTINCT / GROUP BY server-side, so the CLI reads a small result and
-- never hits the PostgREST 1000-row cap.
--
-- Semantics / exclusions:
--   * Soft-deleted experiments are excluded (WHERE e.deleted_at IS NULL) as
--     defence-in-depth: bloom_admin has USING(true) on cyl_experiments, so without
--     this it would see accessions/counts from deleted experiments.
--   * accession_id is nullable on cyl_plants: plants with no accession belong to no
--     accession and are therefore excluded from BOTH views (they cannot be grouped
--     per-accession). This means a plant_count here can be lower than the raw
--     cyl_plants total for an experiment where some plants are unassigned.
--   * species_id is nullable on cyl_experiments: species is LEFT-joined, so accessions
--     in experiments without a species still appear (species_id/species_name NULL).

-- (A) Distinct accessions used in each (non-deleted) experiment.
create or replace view public.cyl_experiment_accessions
with (security_invoker = on) as
select distinct
  e.id as experiment_id,
  p.accession_id,
  a.name as accession_name
from cyl_plants p
join cyl_waves w on w.id = p.wave_id
join cyl_experiments e on e.id = w.experiment_id
join accessions a on a.id = p.accession_id
where e.deleted_at is null
order by e.id, a.name;

-- (B) Plant count per accession, per species (across non-deleted experiments).
create or replace view public.cyl_accession_sample_counts
with (security_invoker = on) as
select
  sp.id as species_id,
  sp.common_name as species_name,
  p.accession_id,
  a.name as accession_name,
  count(*)::bigint as plant_count
from cyl_plants p
join cyl_waves w on w.id = p.wave_id
join cyl_experiments e on e.id = w.experiment_id
join accessions a on a.id = p.accession_id
left join species sp on sp.id = e.species_id
where e.deleted_at is null
group by sp.id, sp.common_name, p.accession_id, a.name
order by sp.common_name, a.name;

-- Index the accession FK the views filter/group on (cyl_plants only indexed wave_id).
create index if not exists cyl_plants_accession_id_idx on public.cyl_plants (accession_id);

-- Grants mirror the peer read-model views: no anon (all access requires auth), and
-- bloom_writer inherits bloom_user so it is not listed explicitly. Supabase's
-- default privileges auto-grant SELECT to anon on new public views, so omitting
-- anon from the grant is not enough — revoke it explicitly on both.
grant select on public.cyl_experiment_accessions
  to authenticated, bloom_user, bloom_admin, bloom_agent;
revoke all on public.cyl_experiment_accessions from anon;
grant select on public.cyl_accession_sample_counts
  to authenticated, bloom_user, bloom_admin, bloom_agent;
revoke all on public.cyl_accession_sample_counts from anon;
