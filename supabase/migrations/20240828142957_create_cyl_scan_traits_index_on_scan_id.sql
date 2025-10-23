DROP INDEX IF EXISTS idx_cyl_scan_traits;
CREATE INDEX idx_cyl_scan_traits ON cyl_scan_traits(scan_id);
