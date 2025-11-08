-- Make sure cyl_qc_sets.name is unique

ALTER TABLE cyl_qc_sets
ADD CONSTRAINT unique_cyl_qc_sets_name
UNIQUE (name);
