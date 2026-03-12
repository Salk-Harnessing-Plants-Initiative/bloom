-- Migration: scrna_cells_table_add_cluster_column
-- Created: Sat Jan 24 16:52:26 PST 2026

-- Write your SQL migration here
ALTER TABLE scrna_cells
ADD COLUMN replicate text;