-- Returns the orthogroup for each result gene, plus whether it matches the query gene's orthogroup
create or replace function get_orthogroup_info(
  query_gene_id text,
  result_gene_ids text[]
)
returns table (
  gene_id text,
  orthogroup text,
  shared_with_query boolean
)
language sql
as $$
  with query_ogs as (
    select orthogroup from orthogroups where lower(orthogroups.gene_id) = lower(query_gene_id)
  )
  select distinct
    o.gene_id,
    o.orthogroup,
    exists(select 1 from query_ogs q where q.orthogroup = o.orthogroup) as shared_with_query
  from orthogroups o
  where lower(o.gene_id) = any(
    select lower(unnest(result_gene_ids))
  );
$$;
