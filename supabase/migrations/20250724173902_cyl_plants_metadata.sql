create table public.cyl_plant_metadata (
  id uuid primary key default gen_random_uuid(),
  packet text,
  plating_date date,
  position text,
  planting_date date
);