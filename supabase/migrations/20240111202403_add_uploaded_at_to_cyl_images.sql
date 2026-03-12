-- add created_at column to cyl_images table

ALTER TABLE cyl_images ADD COLUMN uploaded_at TIMESTAMPTZ DEFAULT now();

UPDATE cyl_images
SET uploaded_at = cyl_scans.uploaded_at
FROM cyl_scans
WHERE cyl_images.scan_id = cyl_scans.id;
