-- Orthogroup mappings from OrthoFinder (via HPI OrthoBrowser searchkeys.tsv)
-- Maps gene IDs to orthogroup IDs for cross-referencing with embedding KNN results

create table orthogroups (
  id bigserial primary key,
  gene_id text not null,             -- normalized gene ID (matches proteins.gene_id, case-insensitive)
  species text not null,             -- species name (matches proteins.species)
  orthogroup text not null,          -- e.g. "OG0004787"
  raw_gene_id text not null          -- original gene ID from orthobrowser
);

create index idx_orthogroups_gene_id on orthogroups (gene_id);
create index idx_orthogroups_orthogroup on orthogroups (orthogroup);
create index idx_orthogroups_species on orthogroups (species);

-- Given a query gene, find all KNN result genes that share an orthogroup
create or replace function get_orthogroup_matches(
  query_gene_id text,
  result_gene_ids text[]
)
returns table (
  gene_id text,
  orthogroup text
)
language sql
as $$
  with query_ogs as (
    select orthogroup from orthogroups where lower(orthogroups.gene_id) = lower(query_gene_id)
  )
  select distinct o.gene_id, o.orthogroup
  from orthogroups o
  inner join query_ogs q on o.orthogroup = q.orthogroup
  where lower(o.gene_id) = any(
    select lower(unnest(result_gene_ids))
  );
$$;
