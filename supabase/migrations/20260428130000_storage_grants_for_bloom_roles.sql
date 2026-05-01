-- Grant storage schema access to bloom_user, bloom_admin, bloom_agent.
-- Without this, storage-api SET ROLE-ing into these roles cannot read
-- storage.objects, which blocks all signed URL generation.

BEGIN;

GRANT USAGE ON SCHEMA storage TO bloom_user, bloom_admin, bloom_agent;

-- bloom_user: read + write objects, read buckets
GRANT SELECT, INSERT, UPDATE ON storage.objects TO bloom_user;
GRANT SELECT ON storage.buckets TO bloom_user;

-- bloom_admin: full CRUD
GRANT ALL ON storage.objects, storage.buckets TO bloom_admin;

-- bloom_agent: read-only
GRANT SELECT ON storage.objects, storage.buckets TO bloom_agent;

-- Sequences (for any auto-incrementing IDs)
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA storage TO bloom_user, bloom_admin;

COMMIT;
