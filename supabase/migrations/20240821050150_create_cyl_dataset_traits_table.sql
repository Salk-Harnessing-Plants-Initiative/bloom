CREATE TABLE cyl_dataset_traits (
    dataset_id BIGINT NOT NULL REFERENCES cyl_datasets(id),
    trait_id BIGINT NOT NULL REFERENCES cyl_scan_traits(id),
    PRIMARY KEY (dataset_id, trait_id)
);

ALTER TABLE cyl_dataset_traits ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can select cyl_dataset_traits" ON cyl_dataset_traits;

CREATE POLICY "Authenticated users can select cyl_dataset_traits"
ON cyl_dataset_traits AS permissive
FOR SELECT TO authenticated
USING (true);

DROP POLICY IF EXISTS "Authenticated users can insert cyl_dataset_traits" ON cyl_dataset_traits;

CREATE POLICY "Authenticated users can insert cyl_dataset_traits"
ON cyl_dataset_traits AS permissive
FOR INSERT TO authenticated
WITH CHECK (true);
