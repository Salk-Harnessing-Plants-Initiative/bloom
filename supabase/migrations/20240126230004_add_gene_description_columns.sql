-- add genes.short_description and genes.long_description columns

ALTER TABLE genes ADD COLUMN short_description text;
ALTER TABLE genes ADD COLUMN long_description text;
