ALTER TABLE cyl_experiments ADD CONSTRAINT cyl_experiments_scientist_uniqueness UNIQUE (scientist_id, name);
