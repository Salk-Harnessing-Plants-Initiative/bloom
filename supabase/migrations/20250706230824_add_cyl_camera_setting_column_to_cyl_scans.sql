alter table public.cyl_scans
add column cyl_camera_settings_id uuid references public.cyl_camera_settings(id);