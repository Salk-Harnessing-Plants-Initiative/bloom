CREATE TABLE IF NOT EXISTS plates_source_table (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMP DEFAULT NOW(),
    file_path TEXT UNIQUE
);

