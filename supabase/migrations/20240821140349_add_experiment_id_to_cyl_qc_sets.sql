-- add experiment_id to cyl_qc_sets

ALTER TABLE cyl_qc_sets ADD COLUMN experiment_id BIGINT REFERENCES cyl_experiments(id);