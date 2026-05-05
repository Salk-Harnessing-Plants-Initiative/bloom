-- Rollback for 20260504000002_grant_all_scope_reduction.sql
-- Restores the over-broad GRANT ALL state on bloom_admin.

BEGIN;

GRANT TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public TO bloom_admin;
GRANT TRUNCATE, REFERENCES, TRIGGER ON storage.objects, storage.buckets TO bloom_admin;

ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT TRUNCATE, REFERENCES, TRIGGER ON TABLES TO bloom_admin;

COMMIT;
