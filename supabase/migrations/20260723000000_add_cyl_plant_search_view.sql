-- Flattened, searchable plant row backing the cylinder search bar (barcode /
-- accession name / species). One row per plant with its barcode, accession name,
-- species, and experiment, so the UI can match a free-text term across all of them
-- with a single query (and resolve a pasted barcode list in one call).
--
-- security_invoker = on so it runs under the caller's RLS. LEFT joins so a plant
-- with no wave/experiment/accession still appears (any barcode findable before the
-- view stays findable); soft-deleted experiments are excluded — with the LEFT join,
-- `e.deleted_at IS NULL` is true both for a plant with no experiment and for a plant
-- whose experiment is live, and false only when the experiment is soft-deleted.
--
-- Supabase's default privileges auto-grant SELECT to anon on new public views, so
-- revoke it explicitly (omitting anon from the grant is not enough).

create or replace view public.cyl_plant_search
with (security_invoker = on) as
select
  p.id           as plant_id,
  p.qr_code,
  p.accession_id,
  a.name         as accession_name,
  e.id           as experiment_id,
  e.name         as experiment_name,
  sp.id          as species_id,
  sp.common_name as species_name
from cyl_plants p
left join cyl_waves       w  on w.id  = p.wave_id
left join cyl_experiments e  on e.id  = w.experiment_id
left join accessions      a  on a.id  = p.accession_id
left join species         sp on sp.id = e.species_id
where e.deleted_at is null;

grant select on public.cyl_plant_search
  to authenticated, bloom_user, bloom_admin, bloom_agent;
revoke all on public.cyl_plant_search from anon;
