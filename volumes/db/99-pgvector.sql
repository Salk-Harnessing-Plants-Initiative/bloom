-- Init script to enable pgvector during database initialization
-- This file will be executed by the Postgres image entrypoint when the

CREATE EXTENSION IF NOT EXISTS vector;
