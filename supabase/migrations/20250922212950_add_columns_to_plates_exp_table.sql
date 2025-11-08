ALTER TABLE plates_exp
ADD COLUMN IF NOT EXISTS seedling_per_plate INT NULL,
ADD COLUMN IF NOT EXISTS blob_storage_path UUID NULL REFERENCES plates_source_table(id);
