-- change gene_candidates.translation_approval_date to a date
ALTER TABLE gene_candidates ALTER COLUMN translation_approval_date TYPE date USING translation_approval_date::date;