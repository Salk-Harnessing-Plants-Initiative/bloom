CREATE VIEW cyl_wave_timeline AS
    SELECT
        cyl_scans.date_scanned,
        cyl_experiments.name as experiment_name,
        cyl_waves.number as wave_number,
        count(*)
    FROM cyl_scans
    JOIN cyl_plants ON cyl_scans.plant_id = cyl_plants.id
    JOIN cyl_waves ON cyl_plants.wave_id = cyl_waves.id
    JOIN cyl_experiments ON cyl_waves.experiment_id = cyl_experiments.id
    GROUP BY (cyl_experiments.name, cyl_waves.number, cyl_scans.date_scanned)
    ORDER BY cyl_scans.date_scanned DESC;
