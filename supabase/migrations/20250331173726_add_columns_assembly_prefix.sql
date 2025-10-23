-- Add prefix and HPI Assembly column

ALTER TABLE assemblies
ADD COLUMN prefix TEXT DEFAULT NULL,
ADD COLUMN hpi_assembly TEXT DEFAULT NULL;
