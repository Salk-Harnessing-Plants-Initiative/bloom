CREATE VIEW cyl_scan_timeline AS
    SELECT cyl_scans.date_scanned, COUNT(*) FROM cyl_scans GROUP BY cyl_scans.date_scanned ORDER BY cyl_scans.date_scanned DESC;
