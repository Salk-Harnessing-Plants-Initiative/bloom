-- add created_at column to cyl_scans table

ALTER TABLE cyl_scans ADD COLUMN uploaded_at TIMESTAMPTZ DEFAULT now();
