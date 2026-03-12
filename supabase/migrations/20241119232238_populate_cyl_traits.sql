set statement_timeout TO '0';


-- update cyl_traits table to have traits from cyl_image_traits and cyl_scan_traits
INSERT INTO public.cyl_traits (name)
SELECT DISTINCT name
FROM public.cyl_scan_traits
ON CONFLICT (name) DO NOTHING;



--update cyl_image_traits to have trait_id feild refernecing cyl_traits and drop name
-- ALTER TABLE public.cyl_image_traits
-- ADD COLUMN trait_id INT,
-- ADD CONSTRAINT cyl_image_traits_trait_id_fkey FOREIGN KEY (trait_id) REFERENCES public.cyl_traits (id);

-- UPDATE public.cyl_image_traits cit
-- SET trait_id = ct.id
-- FROM public.cyl_traits ct
-- WHERE cit.name = ct.name;

-- ALTER TABLE public.cyl_image_traits DROP COLUMN name;

--update cyl_scan_traits to have trait_id feild refernecing cyl_traits and drop name
ALTER TABLE public.cyl_scan_traits
ADD COLUMN trait_id INT,
ADD CONSTRAINT cyl_scan_traits_trait_id_fkey FOREIGN KEY (trait_id) REFERENCES public.cyl_traits (id);

UPDATE public.cyl_scan_traits cst
SET trait_id = ct.id
FROM public.cyl_traits ct
WHERE cst.name = ct.name;

ALTER TABLE public.cyl_scan_traits
DROP CONSTRAINT scan_source_name_uniqueness;

ALTER TABLE public.cyl_scan_traits
ADD CONSTRAINT scan_source_trait_uniqueness UNIQUE (scan_id, source_id, trait_id);

DROP VIEW IF EXISTS cyl_scan_trait_names;

CREATE VIEW cyl_scan_trait_names AS
SELECT
    name
FROM
    public.cyl_traits;

ALTER TABLE public.cyl_scan_traits DROP COLUMN name;


-- update get_scan_traits to handle new schema
CREATE OR REPLACE FUNCTION get_scan_traits(experiment_id_ BIGINT, trait_name_ TEXT) RETURNS TABLE (
    scan_id BIGINT,
    date_scanned TEXT,
    plant_age_days INT,
    wave_number INT,
    plant_id BIGINT,
    germ_day INT,
    plant_qr_code TEXT,
    accession_name TEXT,
    trait_name TEXT,
    trait_value FLOAT
)
AS $$
    SELECT
    cyl_scans.id as scan_id,
    cyl_scans.date_scanned as date_scanned,
    cyl_scans.plant_age_days as plant_age_days,
    cyl_waves.number as wave_number,
    cyl_plants.id as plant_id,
    cyl_plants.germ_day as germ_day,
    cyl_plants.qr_code as plant_qr_code,
    accessions.name as accession_name,
    cyl_traits.name as trait_name,
    cyl_scan_traits.value as trait_value
    FROM
    species
    JOIN cyl_experiments on cyl_experiments.species_id = species.id
    JOIN cyl_waves on cyl_waves.experiment_id = cyl_experiments.id
    JOIN cyl_plants on cyl_plants.wave_id = cyl_waves.id
    JOIN accessions on cyl_plants.accession_id = accessions.id
    JOIN cyl_scans on cyl_scans.plant_id = cyl_plants.id
    JOIN cyl_scan_traits on cyl_scan_traits.scan_id = cyl_scans.id
    JOIN cyl_traits on cyl_scan_traits.trait_id = cyl_traits.id
    WHERE
    cyl_experiments.id = experiment_id_ AND
    cyl_traits.name = trait_name_
    ORDER BY
    accession_name, plant_id;
$$ LANGUAGE SQL;

