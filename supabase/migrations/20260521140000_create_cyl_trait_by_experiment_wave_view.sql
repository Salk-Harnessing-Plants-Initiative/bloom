-- Pre-grouped trait aggregates by wave (experiment, wave, trait_name).

CREATE OR REPLACE VIEW public.cyl_trait_by_experiment_wave
WITH (security_invoker = on) AS
SELECT
    e.id            AS experiment_id,
    e.name          AS experiment_name,
    w.id            AS wave_id,
    w.number        AS wave_number,
    ct.name         AS trait_name,
    COUNT(t.value)  AS n,
    AVG(t.value)    AS mean,
    STDDEV(t.value) AS std,
    MIN(t.value)    AS min_value,
    MAX(t.value)    AS max_value
FROM public.cyl_scan_traits t
JOIN public.cyl_traits      ct ON t.trait_id = ct.id
JOIN public.cyl_scans       s  ON t.scan_id = s.id
JOIN public.cyl_plants      p  ON s.plant_id = p.id
JOIN public.cyl_waves       w  ON p.wave_id = w.id
JOIN public.cyl_experiments e  ON w.experiment_id = e.id
WHERE e.deleted = FALSE
GROUP BY e.id, e.name, w.id, w.number, ct.name;

GRANT SELECT ON public.cyl_trait_by_experiment_wave
    TO bloom_agent, bloom_user, bloom_admin, authenticated;
