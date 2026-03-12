-- add ortho_group and ortho_group_row_number columns to gene table

ALTER TABLE genes
ADD COLUMN ortho_group TEXT DEFAULT NULL,
ADD COLUMN standard_name TEXT DEFAULT NULL,
ADD COLUMN ortho_group_row_number INT DEFAULT NULL;
