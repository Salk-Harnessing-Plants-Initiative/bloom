CREATE OR REPLACE FUNCTION assign_partition_numbers()
RETURNS VOID AS $$
BEGIN
    UPDATE genes
    SET ortho_group_row_number = subquery.partition_number
    FROM (
        SELECT gene_id, 
               ROW_NUMBER() OVER (PARTITION BY "ortho_group" ORDER BY gene_id) AS partition_number
        FROM genes
        WHERE "ortho_group" IS NOT NULL
    ) AS subquery
    WHERE genes.gene_id = subquery.gene_id;
END;
$$ LANGUAGE plpgsql;
