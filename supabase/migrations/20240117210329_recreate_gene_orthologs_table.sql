DROP TABLE IF EXISTS gene_orthologs;

-- Creating table 'gene_orthologs'
CREATE TABLE gene_orthologs (
    gene_x TEXT REFERENCES genes(gene_id),
    gene_y TEXT REFERENCES genes(gene_id),
    PRIMARY KEY (gene_x, gene_y),
    CHECK (gene_x <> gene_y)
);

ALTER TABLE gene_orthologs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can select gene_orthologs" ON gene_orthologs;
CREATE POLICY "Authenticated users can select gene_orthologs"
ON gene_orthologs AS permissive
FOR SELECT TO authenticated
USING (true);


-- Function for insert trigger
CREATE OR REPLACE FUNCTION insert_reverse_pair()
RETURNS TRIGGER AS $$
BEGIN
    -- Insert the reverse pair if it does not exist
    IF NOT EXISTS (SELECT 1 FROM gene_orthologs WHERE gene_x = NEW.gene_y AND gene_y = NEW.gene_x) THEN
        INSERT INTO gene_orthologs(gene_x, gene_y) VALUES (NEW.gene_y, NEW.gene_x);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Function for delete trigger
CREATE OR REPLACE FUNCTION delete_reverse_pair()
RETURNS TRIGGER AS $$
BEGIN
    -- Delete the reverse pair if it exists
    DELETE FROM gene_orthologs WHERE gene_x = OLD.gene_y AND gene_y = OLD.gene_x;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql;

-- Create insert trigger
CREATE TRIGGER trigger_insert_reverse_pair
AFTER INSERT ON gene_orthologs
FOR EACH ROW EXECUTE FUNCTION insert_reverse_pair();

-- Create delete trigger
CREATE TRIGGER trigger_delete_reverse_pair
AFTER DELETE ON gene_orthologs
FOR EACH ROW EXECUTE FUNCTION delete_reverse_pair();
