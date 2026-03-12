-- add scientist_id to cyl_experiments (referencing the people table)
ALTER TABLE cyl_experiments ADD COLUMN scientist_id BIGINT REFERENCES people(id);
