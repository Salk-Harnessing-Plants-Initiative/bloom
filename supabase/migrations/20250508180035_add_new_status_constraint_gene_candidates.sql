--dropping existing constraint and adding again
ALTER TABLE gene_candidates
DROP CONSTRAINT gene_candidates_status_check;

ALTER TABLE gene_candidates
ADD CONSTRAINT gene_candidates_status_check
CHECK (status IN (
  'suspected',
  'under-investigation',
  'stopped',
  'in-translation',
  'translation-confirmed'
));

ALTER TABLE gene_candidates
ALTER COLUMN status SET DEFAULT 'suspected';