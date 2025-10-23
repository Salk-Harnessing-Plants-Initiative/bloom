-- add genes.short_id column
ALTER TABLE genes ADD COLUMN short_id text UNIQUE;
