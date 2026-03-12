--adding drymass information to the metadata column
CREATE TABLE IF NOT EXISTS public.cyl_plants_metadata (
  id uuid primary key default gen_random_uuid(),
  group_id text, 
  packet text,
  plating_date date,
  position text,
  planting_date date,
  root_mass double precision,
  shoot_mass double precision,
  plant_count integer,
  avg_root_mass double precision,
  avg_shoot_mass double precision,
  avg_root_shoot_ratio double precision GENERATED ALWAYS AS (
    avg_root_mass / NULLIF(avg_shoot_mass, 0)
  ) STORED,
  tubemass_no_cap_empty_root double precision,
  tubemass_plus_root double precision,
  tubemass_plus_shoot double precision
)
