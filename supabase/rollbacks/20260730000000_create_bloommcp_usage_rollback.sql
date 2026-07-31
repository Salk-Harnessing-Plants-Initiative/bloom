-- Manual rollback for 20260730000000_create_bloommcp_usage.sql
--
-- Drops the record_bloommcp_usage RPC and the bloommcp_usage table (policies and grants go
-- with it). Purely additive forward migration -- nothing else to restore.
--
-- CAUTION: if the langchain-agent sibling change (tracked, not yet filed -- see
-- openspec/changes/add-bloommcp-caller-identity/proposal.md Scope) has shipped by the time this
-- rollback runs, bloommcp_usage may hold real accumulated usage data. This DROP is
-- unconditional and does not back it up; take a `pg_dump -t bloommcp_usage` first if that data
-- is still wanted.

BEGIN;

DROP FUNCTION IF EXISTS public.record_bloommcp_usage(text, text);
DROP TABLE IF EXISTS public.bloommcp_usage;

COMMIT;
