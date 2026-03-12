-- drop the constraint if it already exists
ALTER TABLE cyl_scan_traits ADD CONSTRAINT scan_source_name_uniqueness UNIQUE (scan_id, source_id, name);
