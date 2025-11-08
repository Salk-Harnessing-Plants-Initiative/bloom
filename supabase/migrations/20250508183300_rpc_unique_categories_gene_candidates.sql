create or replace function get_unique_categories()
returns table(category text)
language sql
as $$
  select distinct category
  from gene_candidates
  where category is not null
  order by category;
$$;
