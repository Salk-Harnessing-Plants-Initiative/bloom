ALTER TABLE cyl_qc_set_codes DROP CONSTRAINT cyl_qc_set_codes_pkey;
ALTER TABLE cyl_qc_set_codes ADD PRIMARY KEY (set_id, code_id);