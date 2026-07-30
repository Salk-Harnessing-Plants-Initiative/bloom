-- bloommcp caller-identity usage tracking (bloom#406, openspec
-- add-bloommcp-caller-identity): a rolling per-identity usage aggregate, upserted once per
-- tool call by bloom_mcp.usage.with_usage_recording (contract.wrap.register()'s wrapper) via
-- the new bloom_mcp.supabase_client.call_rpc() seam.
--
-- identity is the resolved X-Bloom-Identity sub claim (a Supabase user UUID), or the literal
-- 'anonymous' when no header was present -- collapsing all unauthenticated usage into one
-- aggregate row (design.md Decision 7). identity stays TEXT rather than Postgres UUID because
-- it must also hold that non-UUID sentinel; the column's integrity instead relies on the
-- application-layer guard in bloom_mcp.identity (UUID-shape + reserved-sentinel rejection at
-- verification time, before anything ever reaches this table) -- see design.md Decision 1.
--
-- This is a rolling aggregate (last-known state per identity), not an append-only log: a
-- repeat call from the same identity overwrites last_action/last_seen and does not preserve
-- the previous action. See design.md Risks -- this matches the issue's own acceptance-criteria
-- shape (identity, first/last seen, request count, last action), not a fuller audit log.
--
-- Policy/grant shape follows two existing precedents rather than inventing a new one:
--   admin_all_bloommcp_usage / agent_read_bloommcp_usage  mirror the gravi_experiments
--       template (20260527180100_create_gravi_experiments_table.sql) -- bloom_admin ALL,
--       bloom_agent SELECT (SELECT is also covered by the standing
--       `ALTER DEFAULT PRIVILEGES ... GRANT SELECT ON TABLES TO bloom_agent` in
--       20260414002000_security_groups.sql:52-54, but the policy is still required for RLS to
--       allow it through).
--   agent_insert_bloommcp_usage / agent_update_bloommcp_usage  mirror
--       agent_insert_bloommcp_data / agent_update_bloommcp_data
--       (20260605000000_create_bloommcp_data_bucket.sql) -- bloom_agent-scoped, unconditional
--       USING/WITH CHECK (true), since there is no natural per-row ownership predicate for a
--       usage-aggregate table. No bloom_user policies -- this table is bloommcp/ops-only, not
--       user-facing.
--
-- record_bloommcp_usage(p_identity, p_action) is the sole write path: an atomic
-- INSERT ... ON CONFLICT upsert (Postgres's standard idiom -- serializes concurrent first-time
-- inserts for the same new identity via the primary-key unique index, so two simultaneous
-- first calls land at request_count = 2, not a lost update or a duplicate-key error). EXECUTE
-- is granted to bloom_agent only -- not bloom_user/bloom_admin/authenticated -- since letting
-- an ordinary authenticated user call this would let them set request_count/last_action for
-- an arbitrary identity string, bypassing the aggregate's integrity.
--
-- Manual rollback: supabase/rollbacks/20260730000000_create_bloommcp_usage_rollback.sql

BEGIN;

CREATE TABLE bloommcp_usage (
    identity      TEXT PRIMARY KEY,
    first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
    request_count BIGINT NOT NULL DEFAULT 1,
    last_action   TEXT
);

ALTER TABLE bloommcp_usage ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS admin_all_bloommcp_usage ON public.bloommcp_usage;
CREATE POLICY admin_all_bloommcp_usage ON public.bloommcp_usage
    FOR ALL TO bloom_admin USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS agent_read_bloommcp_usage ON public.bloommcp_usage;
CREATE POLICY agent_read_bloommcp_usage ON public.bloommcp_usage
    FOR SELECT TO bloom_agent USING (true);

DROP POLICY IF EXISTS agent_insert_bloommcp_usage ON public.bloommcp_usage;
CREATE POLICY agent_insert_bloommcp_usage ON public.bloommcp_usage
    FOR INSERT TO bloom_agent WITH CHECK (true);

DROP POLICY IF EXISTS agent_update_bloommcp_usage ON public.bloommcp_usage;
CREATE POLICY agent_update_bloommcp_usage ON public.bloommcp_usage
    FOR UPDATE TO bloom_agent USING (true) WITH CHECK (true);

-- Table-level GRANTs are checked before RLS. bloom_agent already holds SELECT via the standing
-- default-privilege declaration (20260414002000_security_groups.sql); INSERT/UPDATE do not fall
-- under that declaration's verb list for bloom_agent, so they need an explicit grant here,
-- mirroring 20260605000000_create_bloommcp_data_bucket.sql's identical reasoning for
-- storage.objects.
GRANT INSERT, UPDATE ON public.bloommcp_usage TO bloom_agent;

CREATE OR REPLACE FUNCTION public.record_bloommcp_usage(
    p_identity text,
    p_action   text
) RETURNS void
LANGUAGE sql
SECURITY INVOKER
AS $$
    INSERT INTO public.bloommcp_usage (identity, last_action)
    VALUES (p_identity, p_action)
    ON CONFLICT (identity) DO UPDATE SET
        last_seen = now(),
        request_count = bloommcp_usage.request_count + 1,
        last_action = EXCLUDED.last_action;
$$;

REVOKE EXECUTE ON FUNCTION public.record_bloommcp_usage(text, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.record_bloommcp_usage(text, text) TO bloom_agent;

COMMIT;
