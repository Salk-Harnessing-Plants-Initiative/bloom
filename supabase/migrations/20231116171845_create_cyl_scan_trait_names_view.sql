CREATE VIEW cyl_scan_trait_names AS
  SELECT
    DISTINCT name
  FROM
    cyl_scan_traits
  ORDER BY name;
