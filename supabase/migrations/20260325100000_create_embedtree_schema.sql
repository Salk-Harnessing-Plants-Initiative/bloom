-- EmbedTree: Protein Embedding Phylogenomics Schema
-- Stores 121,619 ESM-2 protein embeddings (1280-dim) across 4 plant species

-- Enable pgvector extension
create extension if not exists vector;

-- =============================================================================
-- 1. Proteins table (main embeddings storage)
-- =============================================================================
create table proteins (
  id bigserial primary key,
  uid text unique not null,          -- e.g. "arabidopsis:AT5G16970"
  species text not null,             -- e.g. "arabidopsis"
  gene_id text not null,             -- e.g. "AT5G16970"
  embedding vector(1280) not null,   -- ESM-2 embedding
  created_at timestamptz default now()
);

-- IVFFlat index for approximate cosine similarity search
create index idx_proteins_embedding on proteins
  using ivfflat (embedding vector_cosine_ops)
  with (lists = 100);

-- B-tree indexes for filtering and lookups
create index idx_proteins_species on proteins (species);
create index idx_proteins_gene_id on proteins (gene_id);
create index idx_proteins_uid on proteins (uid);

-- =============================================================================
-- 2. RBH cache table (pre-computed reciprocal best hits)
-- =============================================================================
create table rbh_cache (
  id bigserial primary key,
  species_1 text not null,
  species_2 text not null,
  metric text not null default 'euclidean',
  rbh_count int not null,
  mean_distance float not null,
  computed_at timestamptz default now(),
  unique (species_1, species_2, metric)
);

-- =============================================================================
-- 3. KNN search function (cosine similarity)
-- =============================================================================
create or replace function knn_search(
  query_uid text,
  match_count int default 20
)
returns table (
  uid text,
  species text,
  gene_id text,
  similarity float
)
language plpgsql
as $$
declare
  query_embedding vector(1280);
begin
  select p.embedding into query_embedding
  from proteins p
  where p.uid = query_uid;

  if query_embedding is null then
    return;
  end if;

  return query
    select
      p.uid,
      p.species,
      p.gene_id,
      1 - (p.embedding <=> query_embedding)::float as similarity
    from proteins p
    order by p.embedding <=> query_embedding
    limit match_count;
end;
$$;

-- =============================================================================
-- 4. Gene search function (partial text match for autocomplete)
-- =============================================================================
create or replace function search_genes(
  partial_id text,
  max_results int default 20
)
returns table (uid text, species text, gene_id text)
language sql
as $$
  select p.uid, p.species, p.gene_id
  from proteins p
  where p.uid ilike '%' || partial_id || '%'
  limit max_results;
$$;
