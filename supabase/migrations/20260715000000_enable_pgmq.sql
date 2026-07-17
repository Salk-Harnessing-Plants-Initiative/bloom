-- Enable pgmq (Postgres Message Queue) — the queue engine behind Supabase Queues.
-- Gives SQS-style durable queues natively in Postgres: send/read with a
-- visibility timeout, retries, and archival, consumed pull-style by a worker.
--
-- Enabling the extension only creates the `pgmq` schema and its management
-- functions; it does NOT create any queues. A follow-up PR adds the queued
-- video-generation route and will create its queue + grant the workflows role
-- the pgmq functions it needs.
--
-- Available since supabase/postgres 15.6.1.143 (dev 15.8.1.060, prod 15.14.1.104
-- both qualify). Idempotent — safe to re-apply.

CREATE EXTENSION IF NOT EXISTS pgmq;
