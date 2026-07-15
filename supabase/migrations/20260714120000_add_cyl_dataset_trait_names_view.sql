-- Distinct trait names per cyl dataset, exposed as a directly-queryable view.
--
-- cyl_dataset_traits holds ONE row per scan x trait measurement
-- (cyl_dataset_traits.trait_id -> cyl_scan_traits.id), so a dataset can accumulate
-- thousands of rows even though it contains only a few dozen distinct traits. Reading
-- that table through PostgREST hits the default 1000-row cap (with no ORDER BY), which
-- can silently and nondeterministically under-report the distinct trait set.
--
-- This view does the DISTINCT join once. Querying it for a single dataset returns only
-- the small trait-name set (well under the cap), and it can be filtered/joined like a
-- table. It stores nothing, so it is always correct with no backfill or sync. If a hot
-- path ever needs it, this can become a MATERIALIZED view with the same interface.
--
-- security_invoker = true so the view runs under the CALLER's RLS (Postgres 15+): a
-- caller sees exactly the datasets/traits their policies allow.

create or replace view public.cyl_dataset_trait_names
with (security_invoker = true) as
select distinct
  dt.dataset_id,
  t.name as trait_name
from cyl_dataset_traits dt
join cyl_scan_traits st on st.id = dt.trait_id
join cyl_traits t on t.id = st.trait_id;

grant select on public.cyl_dataset_trait_names
  to anon, authenticated, bloom_user, bloom_writer, bloom_admin, bloom_agent;
