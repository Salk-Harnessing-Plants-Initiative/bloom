-- make scrna_datasets.scientist_id nullable
ALTER TABLE scrna_datasets ALTER COLUMN scientist_id DROP NOT NULL;
