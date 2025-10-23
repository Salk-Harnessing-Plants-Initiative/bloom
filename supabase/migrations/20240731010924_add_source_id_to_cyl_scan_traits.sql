ALTER TABLE cyl_scan_traits ADD COLUMN source_id BIGINT REFERENCES cyl_trait_sources(id);
