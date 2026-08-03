# Supabase

Reference for how the Supabase stack is configured inside the bloom monorepo: roles, storage buckets, RLS conventions, JWT auth flow, and a few sharp edges worth knowing before you make changes. Update this file when you change something durable about the schema, the roles, or the storage layer.

## Stack shape

Supabase is run as a self-hosted set of containers under
`docker-compose.prod.yml` (used for both prod and staging, with different
`-p` projects). The components used in this repo:

| Container               | Image                         | What it does                                                                        |
| ----------------------- | ----------------------------- | ----------------------------------------------------------------------------------- |
| `db-prod`               | `supabase/postgres:15.x`      | The Postgres database (data + auth schemas + storage schema).                       |
| `kong`                  | `kong:2.8.1`                  | API gateway. Routes `/auth`, `/rest`, `/storage`, `/realtime` to the right backend. |
| `auth` (gotrue)         | `supabase/gotrue:v2.x`        | User authentication, magic links, JWT issuance.                                     |
| `rest` (postgrest)      | `postgrest/postgrest:v12.x`   | Exposes Postgres tables as REST via the JWT-derived role.                           |
| `storage` (storage-api) | `supabase/storage-api:v1.x`   | Object storage HTTP API in front of MinIO.                                          |
| `realtime`              | `supabase/realtime:v2.x`      | Postgres logical replication → WebSocket.                                           |
| `supavisor`             | `supabase/supavisor:2.x`      | Connection pooler.                                                                  |
| `supabase-minio`        | `minio/minio`                 | S3-compatible object store backing `storage`.                                       |
| `meta`                  | `supabase/postgres-meta:v0.x` | Used by Studio for schema introspection.                                            |
| `studio`                | `supabase/studio:2026.x`      | Admin UI.                                                                           |

The browser-facing URL is the one in `.env.{prod,staging}.defaults` (`SUPABASE_PUBLIC_URL`). Internal services talk to `http://kong:8000` via the `supanet` Docker network.

## The four bloom\_\* Postgres roles

The repo defines four custom Postgres roles that the storage / REST APIs switch into based on the JWT's `role` claim.

The mapping happens in the custom access token hook (`supabase/migrations/20260519140000_jwt_hook_read_app_meta_data.sql`).
Every JWT signed with a `role: bloom_X` claim is switched into that Postgres role for the lifetime of the request.

Direct DB login is reserved for the `postgres` superuser — documented on Notion Page.

| Role             | Intent                                       | Table grants on `public.*` (today)                                                                                 | Table grants on `storage.objects`                                                                                                                                                                                        | Notes                                                                                                                                             |
| ---------------- | -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bloom_admin`  | Break-glass: DDL, migrations, manual cleanup | `ALL` on every table (one `admin_all_*` RLS policy per table, `USING (true) WITH CHECK (true)`)                | `DELETE, INSERT, SELECT, UPDATE` + global `admin_all_objects (USING true, WITH CHECK true)`                                                                                                                            | Effectively superuser-equivalent for application surfaces. Don't issue JWTs for this role to user-facing code.                                    |
| `bloom_user`   | Web app users                                | `SELECT` everywhere (58 tables) + a tightly-scoped set of INSERT/UPDATE policies for graviscan/cyl/upload surfaces | `INSERT, SELECT, UPDATE` + per-bucket `user_read_*` policies for cyl-images, images, videos, scrna, exp-progress-logs, graviscan-*, species-illustrations; INSERT/UPDATE only on graviscan-images and graviscan-videos | The role users get when they log in via Supabase Auth. The UPDATE policies on graviscan buckets are missing `WITH CHECK` — see Known Issues.   |
| `bloom_writer` | Ingestion / pipeline writers                 | `SELECT/INSERT/UPDATE` on most public tables — **except** the three cyl pipeline-result tables (`cyl_trait_sources`, `cyl_scan_traits`, `cyl_scan_intermediates`), which are **SELECT-only**: writes there go through the `insert_cyl_result_envelope` RPC (changes D/E) | `INSERT, SELECT, UPDATE` + global `writer_select_objects`, `writer_insert_objects`, `writer_update_objects` — all `USING true` / `WITH CHECK true`                                                            | Mostly a "write anywhere" role (the calling code is the scope), **but for the cyl pipeline-result triad the DB is the scope** — RLS denies direct writes and the SECURITY DEFINER RPC is the sole writer. Use only for trusted ingestion paths. Inherits from `bloom_user`. |
| `bloom_agent`  | LLM agents (langchain-agent, bloommcp)       | `SELECT` only across all public tables (read-only)                                                                 | `SELECT` globally + (after the bloommcp PR) `INSERT, UPDATE` table grants + the bucket-scoped `agent_insert_bloommcp_data` and `agent_update_bloommcp_data` policies for `bloommcp-data` only                    | Read-only**for data tables**. The bloommcp PR carved out a single bucket where the agent can write. No DELETE anywhere.                     |

### Role inheritance

```text
postgres
  ├─ bloom_admin
  ├─ bloom_user
  │   └─ bloom_writer        ← writer inherits user's privileges + its own
  └─ bloom_agent

authenticator
  ├─ bloom_admin
  ├─ bloom_user
  ├─ bloom_writer
  └─ bloom_agent
```

The `authenticator` role is what PostgREST / storage-api connect as. After JWT validation, they `SET ROLE bloom_*` to enter the right scope for the request.

### Write-back RPC (`insert_cyl_result_envelope`)

The sleap-roots pipeline write-back (changes D/E) is the one place where the DB, not the calling code, owns write scope. `public.insert_cyl_result_envelope(envelope jsonb)` is a `SECURITY DEFINER` function (owned by `postgres`, with a pinned `search_path`) that ingests one `sleap-roots-contracts` `ResultEnvelope` and writes it — in a single, idempotent transaction — into `cyl_trait_sources`, `cyl_scan_traits` (via the `cyl_traits` registry) and `cyl_scan_intermediates`. Re-delivery of an already-ingested run is a pure no-op.

`EXECUTE` is revoked from `PUBLIC` and granted only to `bloom_writer`, `service_role`, `bloom_admin`, and `bloom_workflows` (the scoped, non-interactive service identity used by A4 cluster write-back pods). The three target tables have **no** direct INSERT/UPDATE policy for any role except `bloom_admin` (break-glass), so the RPC is the sole sanctioned writer — closing the forgeable-client-INSERT path that the legacy `authenticated` policies left open.

### Pipeline-trigger tables (`cyl_pipeline_runs` / `cyl_pipeline_run_scans`)

Phase 1 of the A4 pipeline-trigger route (`POST /workflows/pipeline`, bloom #11/#404) added `cyl_pipeline_runs` (one row per trigger request) and `cyl_pipeline_run_scans` (one row per enumerated scan). `bloom_workflows` holds `SELECT`+`INSERT` on both — **not** `UPDATE`, since this phase's route only ever inserts; a later phase adds its own small grant migration when a push-based status writer needs it, matching this role's "expand grants only when a new endpoint needs them" convention. The dedup preview also needed two new read paths on **existing** tables: a `SELECT` policy + column-scoped `GRANT SELECT (scan_id, source_id)` on `cyl_scan_traits` and `GRANT SELECT (id, metadata)` on `cyl_trait_sources` (to check all of a scan's prior sources, not just the newest), plus `SELECT (id)`-only existence-check access on `cyl_waves`/`cyl_experiments` (to distinguish "target exists but has zero scans" from "target doesn't exist" — `cyl_scans_extended`'s inner joins can't tell those apart on their own). Both tables are in the `supabase_realtime` publication (the web UI's future live-status panel subscribes without polling).

`enqueue_cyl_pipeline_batch(p_run_id, p_batch_index, p_scan_ids)` is a separate `SECURITY DEFINER` function wrapping a new pgmq queue (see the next section) — its `EXECUTE` is granted to **`bloom_workflows` only**, a narrower grant than the write-back RPC's four-role list above; do not conflate the two.

## pgmq queues

`pgmq` (the Postgres Message Queue extension behind Supabase Queues) is enabled repo-wide (`20260715000000_enable_pgmq.sql`) but creates no queues on its own — each queue is created by whichever change needs it. The convention, established by `cyl_pipeline_dispatch` (Phase 1 of the A4 pipeline-trigger route, bloom #11/#404):

- `pgmq.create('<queue_name>')`, guarded by an existence check (`pgmq.list_queues()`) so re-applying the migration is a no-op.
- Access to the queue is never exposed directly — callers only ever get a purpose-built `SECURITY DEFINER` wrapper function (e.g. `enqueue_cyl_pipeline_batch`), never raw `pgmq.send`/`pgmq.read`.
- `EXECUTE` on each wrapper function is explicitly revoked from `PUBLIC`, `anon`, **and** `authenticated` — not just `PUBLIC`. Supabase's default privileges grant new public-schema functions `EXECUTE` to `anon`/`authenticated` directly, so revoking only from `PUBLIC` leaves both able to call it. `EXECUTE` is then granted only to the specific role that needs it.
- A negative-authorization integration test (`has_function_privilege` for `anon`/`authenticated`/`PUBLIC`, asserting `false`) should exist for every new wrapper function from the start, not discovered as a review follow-up.

## Storage buckets

`bloom-storage` : The single S3 bucket the Supabase Storage API uses as its backend.

**Every logical bucket below is a prefix inside it.**

| Bucket                                               | What it holds                                                                                                             | Public?        | Notes                                                                                                 |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- | -------------- | ----------------------------------------------------------------------------------------------------- |
| `images`                                             | Cylinder phenotyping images                                                                                               | no             | bloom_user has SELECT only.                                                                           |
| `cyl-images`                                         | Cylinder scan images                                                                                                      | no             | bloom_user SELECT.                                                                                    |
| `videos`                                             | Cylinder scan videos                                                                                                      | no             | bloom_user SELECT.                                                                                    |
| `scrna`                                              | scRNA-seq counts JSON                                                                                                     | no             | bloom_user SELECT.                                                                                    |
| `species-illustrations` (hyphen; rename in progress) | Per-species illustration thumbnails                                                                                       | no             | bloom_user SELECT. PR #261 renames this from the legacy `species_illustrations` underscore form.      |
| `experiment-log-images`                              | Images attached to gene-candidate progress logs                                                                           | yes (download) | Anyone can read; only authenticated can write.                                                        |
| `plates-images`                                      | Plate scan thumbnails                                                                                                     | yes (download) |                                                                                                       |
| `plate-blob-storage`                                 | Plate scan large blobs                                                                                                    | yes (download) |                                                                                                       |
| `graviscan-images`                                   | Plate-scanner gravi images                                                                                                | no             | bloom_user INSERT + UPDATE — the only buckets users can write to.                                     |
| `graviscan-videos`                                   | Plate-scanner gravi videos                                                                                                | no             | Same as above.                                                                                        |
| `bloommcp-data` (new)                                | CSV exchange between bloommcp tools and external producers/consumers. Two prefixes:`bloommcp_input/`, `bloommcp_output/`. | no             | Only `bloom_agent` can write, scoped via `agent_insert_bloommcp_data` / `agent_update_bloommcp_data`. |
| `cyl-intermediates` (new)                            | `.slp` blob bytes referenced by `cyl_scan_intermediates.s3_location`, uploaded by `bloomctl cyl ingest-result --predictions-dir` (bloom #407). | no             | `bloom_writer`/`bloom_workflows` SELECT+INSERT+UPDATE (no DELETE); `bloom_agent`/`bloom_user` SELECT only — mirrors the `bloom_workflows`/`videos`-bucket precedent, not the RPC-only lockdown on the `cyl_scan_intermediates` table itself. |
