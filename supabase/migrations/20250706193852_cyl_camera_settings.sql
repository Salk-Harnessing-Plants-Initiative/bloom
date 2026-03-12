create table cyl_camera_settings (
  id uuid primary key default gen_random_uuid(),
  exposure_time integer not null,
  gain integer not null,
  brightness integer not null,
  contrast integer not null,
  gamma integer not null,
  seconds_per_rot integer not null
);

alter table cyl_camera_settings enable row level security;

create policy "Allow insert for authenticated users"
on cyl_camera_settings
for insert
to authenticated
with check (true);

create policy "Allow read for authenticated users"
on cyl_camera_settings
for select
to authenticated
using (true);

create policy "Allow update for authenticated users"
on cyl_camera_settings
for update
to authenticated
using (true)
with check (true);
