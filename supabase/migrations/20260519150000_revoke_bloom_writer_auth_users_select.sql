-- Revoke SELECT on auth.users from bloom_writer.

BEGIN;

REVOKE SELECT ON auth.users FROM bloom_writer;

COMMIT;
