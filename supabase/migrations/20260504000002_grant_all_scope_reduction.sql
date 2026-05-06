-- Migration: grant_all_scope_reduction
--
-- Tighten over-broad GRANT ALL on bloom_admin without changing day-to-day
-- capability:
--   * bloom_admin loses TRUNCATE / REFERENCES / TRIGGER on every public table,
--     on storage.objects + storage.buckets, and on default-privileges for
--     future public tables. Keeps SELECT, INSERT, UPDATE, DELETE.
--   * bloom_user, bloom_agent unchanged.
--
-- Why: TRUNCATE bypasses RLS, TRIGGER is a privilege-escalation vector,
-- REFERENCES is unnecessary. Defense-in-depth — if a bloom_admin token
-- leaks, the blast radius is smaller.

BEGIN;

-- 1. bloom_admin: strip dangerous extras from existing public tables.
REVOKE TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public FROM bloom_admin;

-- 2. bloom_admin: strip dangerous extras from storage tables.
REVOKE TRUNCATE, REFERENCES, TRIGGER ON storage.objects, storage.buckets FROM bloom_admin;

-- 3. Future-proofing: change default privileges so newly-created public tables
--    auto-grant explicit CRUD to bloom_admin instead of ALL.
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE TRUNCATE, REFERENCES, TRIGGER ON TABLES FROM bloom_admin;

COMMIT;
