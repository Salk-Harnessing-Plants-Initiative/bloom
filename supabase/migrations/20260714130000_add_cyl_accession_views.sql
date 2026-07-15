-- Read-model views over cylinder accessions, backing `bloomctl cyl accessions`.
--
-- Chain: cyl_plants (accession_id, wave_id) -> cyl_waves (experiment_id)
--        -> cyl_experiments (species_id) -> species, plus accessions for the name.
-- Built on the base tables (not cyl_plants_extended) so security_invoker applies
-- cleanly to the underlying RLS. Both do their DISTINCT / GROUP BY server-side, so
-- the CLI reads a small result and never hits the PostgREST 1000-row cap.
--
-- NOTE: "samples" counts cyl_plants (one row per physical plant = one biological
-- replicate). If imaging events are wanted instead, count cyl_scans via the plant.

-- (A) Distinct accessions used in each experiment.
create or replace view public.cyl_experiment_accessions
with (security_invoker = true) as
select distinct
  e.id as experiment_id,
  p.accession_id,
  a.name as accession_name
from cyl_plants p
join cyl_waves w on w.id = p.wave_id
join cyl_experiments e on e.id = w.experiment_id
join accessions a on a.id = p.accession_id;

-- (B) Sample (plant) count per accession, per species.
create or replace view public.cyl_accession_sample_counts
with (security_invoker = true) as
select
  sp.id as species_id,
  sp.common_name as species_name,
  p.accession_id,
  a.name as accession_name,
  count(*)::bigint as samples
from cyl_plants p
join cyl_waves w on w.id = p.wave_id
join cyl_experiments e on e.id = w.experiment_id
join species sp on sp.id = e.species_id
join accessions a on a.id = p.accession_id
group by sp.id, sp.common_name, p.accession_id, a.name;

grant select on public.cyl_experiment_accessions
  to anon, authenticated, bloom_user, bloom_writer, bloom_admin, bloom_agent;
grant select on public.cyl_accession_sample_counts
  to anon, authenticated, bloom_user, bloom_writer, bloom_admin, bloom_agent;
