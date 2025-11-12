ALTER TABLE cyl_scans
ADD COLUMN scientist_id BIGINT;

ALTER TABLE cyl_scans
ADD CONSTRAINT fk_cyl_scans_scientist
FOREIGN KEY (scientist_id)
REFERENCES cyl_scientists(id);
